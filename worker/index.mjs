const GITHUB_PAGES_ORIGIN = "https://341615387a-create.github.io";

const SYSTEM_PROMPT = `你是“拾刻”，一个陪用户把还没想清楚的念头继续聊下去的对话伙伴。

你的任务不是替用户确定方向，也不是急着给建议、方案或结论。先接住用户刚刚说出的一个具体细节，再贡献一个能让思考自然继续的观察或问题。

规则：
1. 不预设最终答案，不声称知道对话应该走向哪里。
2. 用户有明显偏向时顺着用户；用户完全迷茫时，才临时选择一条可能性较多的线索。
3. 不连续审问。每次最多问一个问题，也允许只回应而不提问。
4. 不把“我不知道”当成需要逼问的答案，可以从原话里的具体名词、动作、场景或感受接着聊。
5. 用户明确想开始行动时，不再劝他等待、搁置或放弃。
6. 用户纠正理解时，以最新表达为准。
7. 使用自然、口语化的中文，通常控制在 80 到 220 字。不要列清单，不使用心理诊断式措辞。`;

function corsHeaders(origin) {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
    "Vary": "Origin",
  };
}

function json(value, status = 200, origin = "") {
  const headers = {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  };
  if (origin) Object.assign(headers, corsHeaders(origin));
  return new Response(JSON.stringify(value), { status, headers });
}

function acceptedOrigin(request) {
  const origin = request.headers.get("Origin") || "";
  const ownOrigin = new URL(request.url).origin;
  return origin === ownOrigin || origin === GITHUB_PAGES_ORIGIN ? origin : "";
}

function normalizedMessages(value) {
  if (!Array.isArray(value)) throw new Error("messages 必须是数组");
  const messages = value.slice(-24).map((message) => {
    const role = message?.role;
    const content = String(message?.content || "").trim();
    if (!['user', 'assistant'].includes(role) || !content || content.length > 4000) {
      throw new Error("消息格式无效");
    }
    return { role, content };
  });
  if (!messages.length || messages[messages.length - 1].role !== "user") {
    throw new Error("最后一条消息必须来自用户");
  }
  if (messages.reduce((sum, message) => sum + message.content.length, 0) > 30000) {
    throw new Error("对话上下文过长");
  }
  return messages;
}

async function callDeepSeek(request, env, origin) {
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders(origin) });
  }
  if (request.method !== "POST") return json({ error: "只支持 POST" }, 405, origin);
  if (!env.DEEPSEEK_API_KEY) return json({ error: "尚未配置 DeepSeek Key" }, 503, origin);
  const length = Number(request.headers.get("Content-Length") || "0");
  if (length > 128000) return json({ error: "请求过大" }, 413, origin);

  let messages;
  try {
    const body = await request.json();
    messages = normalizedMessages(body.messages);
  } catch (error) {
    return json({ error: error instanceof Error ? error.message : "请求格式无效" }, 400, origin);
  }

  let response;
  try {
    response = await fetch("https://api.deepseek.com/chat/completions", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.DEEPSEEK_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: "deepseek-chat",
        messages: [{ role: "system", content: SYSTEM_PROMPT }, ...messages],
        temperature: 0.9,
        max_tokens: 500,
        stream: false,
      }),
    });
  } catch {
    return json({ error: "暂时无法连接 DeepSeek" }, 502, origin);
  }

  if (!response.ok) return json({ error: "DeepSeek 暂时没有返回有效回复" }, 502, origin);
  const result = await response.json();
  const reply = String(result?.choices?.[0]?.message?.content || "").trim();
  if (!reply) return json({ error: "DeepSeek 返回了空回复" }, 502, origin);
  return json({ reply, model: "deepseek-chat" }, 200, origin);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/healthz") return json({ ok: true, model_configured: Boolean(env.DEEPSEEK_API_KEY) });
    if (url.pathname === "/ai") {
      const origin = acceptedOrigin(request);
      if (!origin) return json({ error: "不接受此来源的请求" }, 403);
      return callDeepSeek(request, env, origin);
    }
    return env.ASSETS.fetch(request);
  },
};
