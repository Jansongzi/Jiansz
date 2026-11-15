---
name: jiansz-daily
description: 在当前 Windows 电脑上通过简小松运行已配置的游戏日常一条龙，或查询调度状态。用户明确说“帮我跑今天的日常”“运行简小松一条龙”或“简小松现在跑到哪了”时使用。
---

# 简小松日常一条龙

在 Windows 上，将示例路径替换为实际安装路径，然后通过 PowerShell 执行一次。

```powershell
& "C:\path\to\Jiansz\Jiansz-Daily.exe"
```

用户只想查询时，在命令末尾加 `--status`。读取返回的 JSON 并用 `message` 回复用户。`accepted` 表示已受理，简小松会先检查并更新脚本。`running` 或 `updating` 表示已有操作进行中。进程启动和请求受理都不足以证明日常已经完成。遇到 `error` 或 `blocked` 时报告消息。冷启动由本机已配置的 `Jiansz_AutoRun` 计划任务处理。
