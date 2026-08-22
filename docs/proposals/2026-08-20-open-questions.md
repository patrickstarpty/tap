# 待确认项

1. 产品负责人和首批使用团队；英文全称已确认为 **Test Automation Platform**。
2. 个人 Agentic Test Lab 进入团队 MVP、再进入企业 AKS 的量化退出门槛分别是什么？
3. Test IR v1 首批目标编译器：Selenium、Playwright、Appium、Cucumber、API/Contract 中哪些必须同时交付？
4. Git 仓库模式：每项目独立仓库、单一资产仓库，还是业务代码同仓？
5. Publish Approval 的策略、审批人和权限范围是什么？Agent 已确定只能创建候选 Artifact；远端 branch/PR 只能由 Commit Service 发布，待确认 Lab 是否允许策略自动批准、团队阶段是否一律要求人工批准。
6. BrowserStack 在企业阶段是否允许访问；若允许，数据区域、Local Tunnel、并发和预算是什么？
7. Entra ID、Key Vault、Private Endpoint、模型数据区域和日志保留的组织标准。
8. MySQL/Redis 的具体 PaaS 产品与 SLA，以及 Queue/Event Stream 是否允许引入独立服务。
9. 物理 Device Farm 的设备数量、宿主系统、USB/网络拓扑和远程控制边界。
10. 质量门禁、RPO/RTO、结果收敛延迟、单 Run 成本等目标值需基线测量后审批。
11. LiteLLM 是否只采用无状态 Gateway；若需要其 Virtual Keys/预算/Admin 持久化能力，必须先验证 MySQL/Redis 兼容性，不能静默新增 PostgreSQL。
12. Knowledge Chat 最终采用组织现有 React Design System 还是独立 Next.js shell；选择不得改变 REST/SSE、Entra/BFF 与 Citation 安全契约。
13. Codex Runtime POC 使用 Platform API key、ChatGPT Enterprise access token 还是已开通的 workload identity；数据区域、保留、预算和并发由谁批准？
14. LiteLLM 当前部署是否完整兼容 Codex 所需 Responses streaming、tool、reasoning、compaction、取消与用量语义；若不兼容，是否批准受治理的 direct OpenAI egress？
