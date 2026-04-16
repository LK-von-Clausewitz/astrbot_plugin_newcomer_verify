# astrbot_plugin_newcomer_verify

基于 [AstrBot](https://github.com/AstrBotDevs/AstrBot) + [NapCat](https://github.com/NapNeko/NapCatQQ) 的 QQ 群聊新人入群验证插件。

## 功能简介

- 当有新成员加入 QQ 群聊时，机器人会自动向其发送私聊（临时会话）验证消息。
- 新成员在规定时间内回复私聊消息，即视为通过验证，机器人会在群聊中公布通过结果。
- 若超时未回复，机器人会在群聊中公布超时结果，并 @ 管理员提醒尽快处理。

## 安装方法

1. 将本插件文件夹 `astrbot_plugin_newcomer_verify` 复制到 AstrBot 的插件目录（通常为 `data/plugins/`）。
2. 重启 AstrBot 或在 WebUI 中重载插件。
3. 在 AstrBot WebUI 的「插件配置」中设置本插件参数（如管理员 QQ、超时时间、验证消息等）。

## 配置说明

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `welcome_message` | 向新人发送的私聊验证消息，支持 `{timeout}` 占位符 | 欢迎加入本群！请在 {timeout} 分钟内回复本条消息完成入群验证... |
| `timeout_minutes` | 验证超时时间（分钟） | 10 |
| `admin_qq_list` | 管理员 QQ 号列表，超时后会 @ 这些管理员 | [] |
| `enabled_groups` | 启用新人验证的群号列表，为空则对所有群启用 | [] |
| `pass_announcement` | 通过验证后在群里的公布消息，支持 `{nickname}`、`{user_id}` | ✅ 新人 {nickname}({user_id}) 已完成入群验证... |
| `timeout_announcement` | 超时后在群里的公布消息，支持 `{nickname}`、`{user_id}` | ⚠️ 新人 {nickname}({user_id}) 未在规定时间内完成入群验证... |

## 使用平台

- 仅推荐在 **QQ（aiocqhttp / NapCat）** 平台使用。

## 注意事项

1. 机器人需要能够向新人发送私聊消息。请确保机器人账号未被限制临时会话或私聊权限。
2. 管理员 QQ 号请务必填写正确，否则无法收到超时提醒。
3. 插件数据持久化存储在 AstrBot 的 `data/` 目录下，更新插件时不会丢失。

## 开源协议

MIT License
