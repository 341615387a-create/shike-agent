# GitHub Pages 演示版

仓库已包含 GitHub Actions 工作流。推送到 `master` 后，工作流会把 `web/` 发布为 GitHub Pages。

首次启用：

1. 打开 GitHub 仓库的 **Settings → Pages**。
2. 在 **Build and deployment → Source** 中选择 **GitHub Actions**。
3. 打开仓库的 **Actions**，等待 `Deploy GitHub Pages demo` 变成绿色。
4. 访问 `https://341615387a-create.github.io/shike-agent/`。

这是静态交互演示：不连接 DeepSeek，不包含 API Key。新建对话、元素树、总结、归档和灵感笔记由浏览器内的演示数据层驱动，并保存在该浏览器的 `localStorage` 中。真实模型对话仍使用本地 Python 服务。
