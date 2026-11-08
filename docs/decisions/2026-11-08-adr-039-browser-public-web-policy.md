---
id: ADR-039
status: accepted
date: 2026-11-08
supersedes: []
superseded-by: []
related-rfcs: []
---

# ADR-039：浏览器任务默认访问公开网站

## 背景

FWD 公开报价页面依赖另一公开域名的接口。只允许起始域名使正常页面无法计算报价。2026-11-08 用户明确要求正常浏览无需配置网络许可。

## 决策

Tapper 新建浏览器观察会话与脚本包默认使用 `public_web` 策略，允许公开 HTTP/HTTPS 网站及页面依赖，用户无需列出域名。起始来源仍保存为任务输入，但不充当公网域名白名单。

TAP AI Browser client 声明策略；TAP 非 AI Browser API 持久化、worker 与隔离代理执行。策略随不可变 bundle 保存，零模型回放沿用原策略。旧 API 请求缺少策略时保持 `declared_origins`，数据库迁移给旧 bundle 同样默认值，不扩大历史回放范围。

保留浏览器只能经任务代理出网、公开 IPv4 DNS 校验与连接地址固定、私网/本机/云元数据拒绝、HTTPS 内层 Host/SNI 与 CONNECT 一致性检查。浏览器不获得模型密钥；本机回环部署边界不变。任务代理在完成目标校验后按需签发短期站点证书，不修改全局系统信任。

## 考虑过的方案

逐域名配置会把网页内部实现交给用户维护；自动猜测依赖域名仍会漏掉 CDN 与重定向。因此采用公开网络策略。移除全部网络隔离会让页面访问宿主机或内部服务，不属于用户要求。

## 后果

需要 Browser API、持久化和执行代理共同保存策略，并验证新任务跨域依赖、旧任务兼容、私网拒绝和真实浏览器 TLS。此决定补充 [ADR-037](2026-11-07-adr-037-browser-automation-core-first.md)，与 [ADR-038](2026-11-08-adr-038-browser-agent-and-script-ownership.md)的未来 Agent 分工独立；不代表 FWD 全链路已验收或 browser-use 已实现。
