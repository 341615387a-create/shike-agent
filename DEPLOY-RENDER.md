# 在 Render 公开部署

本项目使用 GitHub 保存源码，Render 运行 Python 服务。`render.yaml` 已配置端口、健康检查、环境变量和持久化磁盘。

## 部署前

- GitHub 仓库可以保持私有；授权 Render 读取 `shike-agent` 即可。
- 不要把 DeepSeek 密钥写进 GitHub、`render.yaml` 或聊天消息。
- 公网模式会用签名的浏览器 Cookie 隔离会话、元素树、个人记忆和灵感笔记；清除浏览器 Cookie 后会得到新的空白空间。
- 默认同一网络每小时最多生成 20 轮。这是基础防滥用，不等同于账户、验证码或计费系统。

## 创建服务

1. 登录 <https://dashboard.render.com/>。
2. 选择 **New + → Blueprint**。
3. 连接 GitHub，并选择私人仓库 `shike-agent`。
4. Render 会读取仓库根目录的 `render.yaml`，显示 `shike-agent` Web Service 和 1 GB 持久化磁盘。
5. 在 `LLM_API_KEY` 提示处填写 DeepSeek API Key。不要填写到其他字段。
6. 确认创建。构建阶段会先运行全部程序测试，通过后启动服务。
7. 部署完成后打开 Render 提供的 `https://...onrender.com` 地址。

## 已启用的公网边界

- 服务监听 Render 提供的 `PORT`，绑定 `0.0.0.0`。
- 只接受 Render 公网域名（以及可选的 `PUBLIC_ORIGIN` 自定义域名）的同源写请求。
- API Key 只从服务器环境变量读取；访客看不到也不能修改模型设置。
- 不同浏览器只能列出和读取自己的会话、个人记忆与灵感笔记。
- `/healthz` 用于 Render 健康检查，不返回用户数据。
- 每个公网 IP 默认每小时最多触发 20 次模型生成。

## 更新

本地完成修改和测试后：

```powershell
git add .
git commit -m "描述这次修改"
git push
```

Render 会从已连接的分支重新构建并部署。

## 限制

当前使用匿名浏览器 Cookie，不是正式账户系统；用户清除 Cookie 或更换设备后无法找回旧记录。IP 限流只能降低意外消耗，不能抵御有意绕过。正式开放推广前，应增加登录、验证码、用户额度、隐私政策、内容与滥用监控，并迁移到支持多实例的托管数据库。
