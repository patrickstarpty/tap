import type { Locale, ProductModule } from "./model";

export interface PrototypeCopy {
  language: {
    en: string;
    zh: string;
  };
  navigation: Record<ProductModule, string> & {
    collapseSidebar: string;
    closeSidebar: string;
    expandSidebar: string;
    newChat: string;
    chatHistory: string;
    product: string;
    athenaTools: string;
    language: string;
    prototypeTeam: string;
    localWorkspace: string;
  };
  chat: {
    startConversation: string;
    conversation: string;
    messageAthena: string;
    messageComposer: string;
    send: string;
    placeholder: string;
    heading: string;
    description: string;
    sourceHint: string;
    suggestedPrompts: string;
    quickPrompts: readonly [string, string, string];
    answer: string;
    noContextNotice: string;
    selectedContextNotice: string;
    selectedContext: string;
    assistant: string;
    questionNavigation: string;
    jumpToQuestion: string;
    showEarlierQuestion: string;
    showEarlierQuestions: string;
    showLaterQuestion: string;
    showLaterQuestions: string;
  };
  sources: {
    heading: string;
    description: string;
    search: string;
    selected: string;
    ready: string;
    loading: string;
    noReadySources: string;
    noResults: string;
    manageKnowledge: string;
    provenanceHint: string;
    immutableRevision: string;
    knowledgeSource: string;
    knowledgeBaseDocument: string;
    pageLocalSource: string;
    collapse: string;
    close: string;
    expand: string;
  };
  composer: {
    addToMessage: string;
    addFromLibrary: string;
    useAgents: string;
    useSkills: string;
    searchLibrary: string;
    searchAgents: string;
    searchSkills: string;
    messageContext: string;
    selectModel: string;
    currentModel: string;
    models: string;
    remove: string;
    close: string;
  };
  catalog: {
    agents: string;
    skills: string;
    agentsDescription: string;
    skillsDescription: string;
    createAgent: string;
    createSkill: string;
    editAgent: string;
    editSkill: string;
    saveAgent: string;
    saveSkill: string;
    name: string;
    description: string;
    instructions: string;
    searchAgents: string;
    searchSkills: string;
    builtIn: string;
    custom: string;
    useInChat: string;
    cancel: string;
    noResults: string;
    agentCatalog: string;
    skillCatalog: string;
  };
  library: {
    heading: string;
    description: string;
    addSource: string;
    all: string;
    knowledgeGraph: string;
    sources: string;
    sourceCount: string;
    typeFilter: string;
    statusFilter: string;
    allTypes: string;
    allStatuses: string;
    clearFilters: string;
    knowledgeGraphImage: string;
    graphSummary: string;
    visibleDocuments: string;
    concepts: string;
    labeledRelationships: string;
    localSourceDescription: string;
    search: string;
    sourceFile: string;
    cancel: string;
    ready: string;
    processing: string;
    failed: string;
    noResults: string;
    illustrative: string;
    communities: string;
    sourceCommunity: string;
    applicationCommunity: string;
    underwritingCommunity: string;
    partiesCommunity: string;
    nodes: string;
    nodeDetails: string;
    selectNode: string;
    community: string;
    relationships: string;
    provenance: string;
    documentNode: string;
    conceptNode: string;
    entityNode: string;
    connections: string;
    zoomIn: string;
    zoomOut: string;
    resetView: string;
    zoomLevel: string;
    extracted: string;
    inferred: string;
    graphNavigationHint: string;
    application: string;
    underwriting: string;
    healthDisclosure: string;
    beneficiary: string;
    applicant: string;
    policy: string;
    coverage: string;
    premium: string;
    riskAssessment: string;
    requires: string;
    informs: string;
    names: string;
    supports: string;
    submits: string;
    creates: string;
    evaluates: string;
    determines: string;
  };
  artifacts: {
    bddPlanReady: string;
    automationReady: string;
    scenariosDraft: string;
    automationSummary: string;
    feature: string;
    bddPlanLabel: string;
    automationLabel: string;
    scenario: string;
    keywordSeparator: string;
    given: string;
    when: string;
    then: string;
    completeScenario: string;
    completeGiven: string;
    completeWhen: string;
    completeThen: string;
    disclosureScenario: string;
    disclosureGiven: string;
    disclosureWhen: string;
    disclosureThen: string;
    highCoverageScenario: string;
    highCoverageGiven: string;
    highCoverageWhen: string;
    highCoverageThen: string;
  };
  testManagement: {
    heading: string;
    description: string;
    newTestPlan: string;
    testPlan: string;
    testData: string;
    searchPlans: string;
    importedFromAthena: string;
    createdManually: string;
    draft: string;
    importToTestPlan: string;
    importBddAsTestPlan: string;
    testPlans: string;
    testPlanCount: string;
    reusableTestData: string;
    testDataEmpty: string;
    newDataSet: string;
    nameColumn: string;
    scenariosColumn: string;
    sourceColumn: string;
    statusColumn: string;
    sections: string;
  };
  lowCode: {
    heading: string;
    description: string;
    saveDraft: string;
    saved: string;
    automationSteps: string;
    addStep: string;
    deleteStep: string;
    generatedScript: string;
    openInLowCode: string;
    updatesWithEveryStep: string;
    action: string;
    elementOrUrl: string;
    value: string;
    steps: string;
    emptyTitle: string;
    emptyDescription: string;
    startInAthena: string;
    automationStep: string;
    actionForStep: string;
    elementForStep: string;
    valueForStep: string;
    elementPlaceholder: string;
    optional: string;
    navigate: string;
    click: string;
    fill: string;
    assert: string;
    wait: string;
  };
}

export const PROTOTYPE_COPY = {
  en: {
    language: { en: "English", zh: "中文" },
    navigation: {
      athena: "Athena",
      agents: "Agent",
      skills: "Skills",
      library: "Library",
      "test-management": "Test Management",
      "low-code": "Low Code Automation",
      collapseSidebar: "Collapse sidebar",
      closeSidebar: "Close sidebar",
      expandSidebar: "Expand sidebar",
      newChat: "New chat",
      chatHistory: "Chat history",
      product: "Product",
      athenaTools: "Athena tools",
      language: "Language",
      prototypeTeam: "Prototype team",
      localWorkspace: "Local workspace",
    },
    chat: {
      startConversation: "Start a conversation",
      conversation: "Conversation",
      messageAthena: "Message Athena",
      messageComposer: "Message composer",
      send: "Send",
      placeholder: "Ask about life insurance or testing...",
      heading: "What can I do for you?",
      description:
        "Ask about life insurance, create BDD test cases, or build an automation.",
      sourceHint: "Each turn records the knowledge context you select.",
      suggestedPrompts: "Suggested prompts",
      quickPrompts: [
        "Summarize the life insurance underwriting rules",
        "Create BDD test cases for life insurance underwriting",
        "Generate an automation script for a life insurance application",
      ],
      answer:
        "This prototype response says that a life insurance application commonly includes identity details for the policyholder and insured person, health disclosures, beneficiary information, and payment details.",
      noContextNotice:
        "No knowledge context was selected for this turn. This prototype output uses built-in demo content.",
      selectedContextNotice:
        "Context was selected for this turn. This prototype records this selection but does not verify document use.",
      selectedContext: "Selected context",
      assistant: "Athena assistant",
      questionNavigation: "Questions in this conversation",
      jumpToQuestion: "Jump to question",
      showEarlierQuestion: "Show {count} earlier question",
      showEarlierQuestions: "Show {count} earlier questions",
      showLaterQuestion: "Show {count} later question",
      showLaterQuestions: "Show {count} later questions",
    },
    sources: {
      heading: "Knowledge sources",
      description: "Choose what Athena can use.",
      search: "Search knowledge sources",
      selected: "selected",
      ready: "Ready",
      loading: "Loading sources",
      noReadySources: "No ready sources",
      noResults: "No matching sources",
      manageKnowledge: "Manage knowledge",
      provenanceHint:
        "Answers and generated assets record the source context selected for each turn.",
      immutableRevision: "immutable revision",
      knowledgeSource: "Knowledge source",
      knowledgeBaseDocument: "Knowledge base document",
      pageLocalSource: "Page-local Library source",
      collapse: "Collapse Knowledge sources",
      close: "Close Knowledge sources",
      expand: "Expand Knowledge sources",
    },
    composer: {
      addToMessage: "Add to message",
      addFromLibrary: "Add from Library",
      useAgents: "Use Agents",
      useSkills: "Use Skills",
      searchLibrary: "Search library",
      searchAgents: "Search agents",
      searchSkills: "Search skills",
      messageContext: "Message context",
      selectModel: "Select model",
      currentModel: "current model",
      models: "Models",
      remove: "Remove",
      close: "Close",
    },
    catalog: {
      agents: "Agents",
      skills: "Skills",
      agentsDescription:
        "Create focused collaborators for repeatable workflows.",
      skillsDescription:
        "Keep reusable instructions ready for every conversation.",
      createAgent: "Create agent",
      createSkill: "Create skill",
      editAgent: "Edit agent",
      editSkill: "Edit skill",
      saveAgent: "Save agent",
      saveSkill: "Save skill",
      name: "Name",
      description: "Description",
      instructions: "Instructions",
      searchAgents: "Search agents",
      searchSkills: "Search skills",
      builtIn: "Built-in",
      custom: "Custom",
      useInChat: "Use in chat",
      cancel: "Cancel",
      noResults: "No matching items",
      agentCatalog: "Agent catalog",
      skillCatalog: "Skill catalog",
    },
    library: {
      heading: "Library",
      description:
        "Browse source material and explore its curated domain context.",
      addSource: "Add source",
      all: "All",
      knowledgeGraph: "Knowledge Graph",
      sources: "Library sources",
      sourceCount: "sources",
      typeFilter: "Type",
      statusFilter: "Status",
      allTypes: "All types",
      allStatuses: "All statuses",
      clearFilters: "Clear filters",
      knowledgeGraphImage: "Life insurance knowledge graph",
      graphSummary: "Knowledge graph summary",
      visibleDocuments: "Visible documents",
      concepts: "Concepts",
      labeledRelationships: "Labeled relationships",
      localSourceDescription: "Local source · page-only",
      search: "Search library",
      sourceFile: "Source file",
      cancel: "Cancel",
      ready: "Ready",
      processing: "Processing",
      failed: "Failed",
      noResults: "No matching sources",
      illustrative:
        "Illustrative view — interactive relationships and communities are curated, not computed.",
      communities: "Communities",
      sourceCommunity: "Sources",
      applicationCommunity: "Application",
      underwritingCommunity: "Underwriting",
      partiesCommunity: "Parties",
      nodes: "nodes",
      nodeDetails: "Node details",
      selectNode: "Select a node to inspect its relationships.",
      community: "Community",
      relationships: "Relationships",
      provenance: "Provenance",
      documentNode: "Document",
      conceptNode: "Concept",
      entityNode: "Entity",
      connections: "connections",
      zoomIn: "Zoom in",
      zoomOut: "Zoom out",
      resetView: "Reset view",
      zoomLevel: "Zoom level",
      extracted: "EXTRACTED",
      inferred: "INFERRED",
      graphNavigationHint:
        "Drag to pan, use the controls to zoom, and select a node to inspect its relationships.",
      application: "Life insurance application",
      underwriting: "Underwriting",
      healthDisclosure: "Health disclosure",
      beneficiary: "Beneficiary",
      applicant: "Applicant",
      policy: "Policy",
      coverage: "Coverage",
      premium: "Premium",
      riskAssessment: "Risk assessment",
      requires: "requires",
      informs: "informs",
      names: "names",
      supports: "supports",
      submits: "submits",
      creates: "creates",
      evaluates: "evaluates",
      determines: "determines",
    },
    artifacts: {
      bddPlanReady: "BDD test plan ready",
      automationReady: "Automation draft ready",
      scenariosDraft: "3 scenarios · Draft",
      automationSummary: "BDD scenario + 6 automation steps",
      feature: "Feature: Life insurance application underwriting",
      bddPlanLabel: "Generated BDD test plan",
      automationLabel: "Generated automation",
      scenario: "Scenario",
      keywordSeparator: ": ",
      given: "Given",
      when: "When",
      then: "Then",
      completeScenario: "Complete application enters underwriting",
      completeGiven:
        "an adult applicant with completed identity and health declarations",
      completeWhen: "the applicant submits a complete term life application",
      completeThen: 'the application status should be "Pending underwriting"',
      disclosureScenario: "Missing health disclosure is blocked",
      disclosureGiven: "mandatory health disclosure answers are missing",
      disclosureWhen: "the applicant submits the life insurance application",
      disclosureThen: "the application should show a validation error",
      highCoverageScenario: "High coverage requires manual review",
      highCoverageGiven:
        "the requested sum assured exceeds the straight-through limit",
      highCoverageWhen: "the applicant submits the life insurance application",
      highCoverageThen:
        'the application status should be "Additional review required"',
    },
    testManagement: {
      heading: "Test Management",
      description: "Plan coverage and reusable data in one workspace.",
      newTestPlan: "New Test Plan",
      testPlan: "Test Plan",
      testData: "Test Data",
      searchPlans: "Search plans",
      importedFromAthena: "Imported from Athena",
      createdManually: "Created manually",
      draft: "Draft",
      importToTestPlan: "Import to Test Plan",
      importBddAsTestPlan: "Import BDD as Test Plan",
      testPlans: "Test plans",
      testPlanCount: "test plans",
      reusableTestData: "Reusable test data",
      testDataEmpty: "Test data sets will appear here.",
      newDataSet: "New data set",
      nameColumn: "Name",
      scenariosColumn: "Scenarios",
      sourceColumn: "Source",
      statusColumn: "Status",
      sections: "Test Management sections",
    },
    lowCode: {
      heading: "Life insurance application automation",
      description: "Generated by Athena · Playwright · Draft",
      saveDraft: "Save draft",
      saved: "Saved",
      automationSteps: "Automation steps",
      addStep: "Add step",
      deleteStep: "Delete step",
      generatedScript: "Generated script",
      openInLowCode: "Open in Low Code Automation",
      updatesWithEveryStep: "Updates with every step",
      action: "Action",
      elementOrUrl: "Element or URL",
      value: "Value",
      steps: "steps",
      emptyTitle: "No automation draft yet",
      emptyDescription:
        "Ask Athena to generate an automation, then open that draft here to edit its steps.",
      startInAthena: "Start in Athena",
      automationStep: "Automation step",
      actionForStep: "Action for step",
      elementForStep: "Element for step",
      valueForStep: "Value for step",
      elementPlaceholder: "CSS selector, text, or URL",
      optional: "Optional",
      navigate: "Navigate",
      click: "Click",
      fill: "Fill",
      assert: "Assert",
      wait: "Wait",
    },
  },
  zh: {
    language: { en: "English", zh: "中文" },
    navigation: {
      athena: "Athena",
      agents: "智能体",
      skills: "技能",
      library: "知识库",
      "test-management": "测试管理",
      "low-code": "低代码自动化",
      collapseSidebar: "收起侧边栏",
      closeSidebar: "关闭侧边栏",
      expandSidebar: "展开侧边栏",
      newChat: "新建对话",
      chatHistory: "对话历史",
      product: "产品",
      athenaTools: "Athena 工具",
      language: "语言",
      prototypeTeam: "原型团队",
      localWorkspace: "本地工作区",
    },
    chat: {
      startConversation: "开始对话",
      conversation: "对话",
      messageAthena: "向 Athena 发送消息",
      messageComposer: "消息编辑器",
      send: "发送",
      placeholder: "询问寿险业务或测试问题...",
      heading: "我能为您做什么？",
      description: "询问寿险业务、创建 BDD 测试用例，或构建自动化流程。",
      sourceHint: "每轮对话都会记录您选择的知识上下文。",
      suggestedPrompts: "推荐提示词",
      quickPrompts: [
        "总结寿险新单核保规则",
        "为寿险新单核保创建 BDD 测试用例",
        "为寿险投保申请生成自动化脚本",
      ],
      answer:
        "此原型回答显示：寿险投保通常包含投保人和被保险人身份资料、健康告知、受益人信息以及缴费资料。",
      noContextNotice: "此轮对话未选择知识上下文。此原型输出使用内置演示内容。",
      selectedContextNotice:
        "此轮对话已选择知识上下文。原型仅记录该选择，不验证是否使用了文档内容。",
      selectedContext: "已选上下文",
      assistant: "Athena 助手",
      questionNavigation: "本次对话中的问题",
      jumpToQuestion: "跳转到问题",
      showEarlierQuestion: "显示上方另外 {count} 个问题",
      showEarlierQuestions: "显示上方另外 {count} 个问题",
      showLaterQuestion: "显示下方另外 {count} 个问题",
      showLaterQuestions: "显示下方另外 {count} 个问题",
    },
    sources: {
      heading: "知识来源",
      description: "选择 Athena 可以使用的来源。",
      search: "搜索知识来源",
      selected: "已选择",
      ready: "已就绪",
      loading: "正在加载来源",
      noReadySources: "没有可用来源",
      noResults: "没有匹配的来源",
      manageKnowledge: "管理知识库",
      provenanceHint: "回答和生成的资产会记录每轮对话选择的来源上下文。",
      immutableRevision: "不可变版本",
      knowledgeSource: "知识来源",
      knowledgeBaseDocument: "知识库文档",
      pageLocalSource: "仅当前页面的知识库来源",
      collapse: "收起知识来源",
      close: "关闭知识来源",
      expand: "展开知识来源",
    },
    composer: {
      addToMessage: "添加到消息",
      addFromLibrary: "从知识库添加",
      useAgents: "使用智能体",
      useSkills: "使用技能",
      searchLibrary: "搜索知识库",
      searchAgents: "搜索智能体",
      searchSkills: "搜索技能",
      messageContext: "消息上下文",
      selectModel: "选择模型",
      currentModel: "当前模型",
      models: "模型",
      remove: "移除",
      close: "关闭",
    },
    catalog: {
      agents: "智能体",
      skills: "技能",
      agentsDescription: "为可重复的工作流创建专注的协作者。",
      skillsDescription: "将可复用指令应用于每一次对话。",
      createAgent: "创建智能体",
      createSkill: "创建技能",
      editAgent: "编辑智能体",
      editSkill: "编辑技能",
      saveAgent: "保存智能体",
      saveSkill: "保存技能",
      name: "名称",
      description: "描述",
      instructions: "指令",
      searchAgents: "搜索智能体",
      searchSkills: "搜索技能",
      builtIn: "内置",
      custom: "自定义",
      useInChat: "在对话中使用",
      cancel: "取消",
      noResults: "没有匹配项",
      agentCatalog: "智能体目录",
      skillCatalog: "技能目录",
    },
    library: {
      heading: "知识库",
      description: "浏览知识来源，并探索经过编排的领域上下文。",
      addSource: "添加来源",
      all: "全部",
      knowledgeGraph: "知识图谱",
      sources: "知识库来源",
      sourceCount: "个来源",
      typeFilter: "类型",
      statusFilter: "状态",
      allTypes: "全部类型",
      allStatuses: "全部状态",
      clearFilters: "清除筛选",
      knowledgeGraphImage: "寿险知识图谱",
      graphSummary: "知识图谱摘要",
      visibleDocuments: "可见文档",
      concepts: "概念",
      labeledRelationships: "已标注关系",
      localSourceDescription: "本地来源 · 仅当前页面",
      search: "搜索知识库",
      sourceFile: "来源文件",
      cancel: "取消",
      ready: "已就绪",
      processing: "处理中",
      failed: "失败",
      noResults: "没有匹配的来源",
      illustrative: "交互原型 — 关系和社区由本原型编排，并非计算所得。",
      communities: "社区",
      sourceCommunity: "来源",
      applicationCommunity: "投保申请",
      underwritingCommunity: "核保",
      partiesCommunity: "参与方",
      nodes: "个节点",
      nodeDetails: "节点详情",
      selectNode: "选择节点以查看其关系。",
      community: "社区",
      relationships: "关系",
      provenance: "来源依据",
      documentNode: "文档",
      conceptNode: "概念",
      entityNode: "实体",
      connections: "个关联",
      zoomIn: "放大",
      zoomOut: "缩小",
      resetView: "重置视图",
      zoomLevel: "缩放比例",
      extracted: "已抽取",
      inferred: "推断",
      graphNavigationHint: "拖动以平移画布，使用控件缩放，并选择节点查看关系。",
      application: "寿险投保",
      underwriting: "核保",
      healthDisclosure: "健康告知",
      beneficiary: "受益人",
      applicant: "投保人",
      policy: "保单",
      coverage: "保额",
      premium: "保费",
      riskAssessment: "风险评估",
      requires: "需要",
      informs: "影响",
      names: "指定",
      supports: "支持",
      submits: "提交",
      creates: "生成",
      evaluates: "评估",
      determines: "决定",
    },
    artifacts: {
      bddPlanReady: "BDD 测试计划已就绪",
      automationReady: "自动化草稿已就绪",
      scenariosDraft: "3 个场景 · 草稿",
      automationSummary: "BDD 场景 + 6 个自动化步骤",
      feature: "功能：寿险投保申请核保",
      bddPlanLabel: "生成的 BDD 测试计划",
      automationLabel: "生成的自动化流程",
      scenario: "场景",
      keywordSeparator: "：",
      given: "假如",
      when: "当",
      then: "那么",
      completeScenario: "完整申请进入核保",
      completeGiven: "成年申请人已完成身份资料和健康告知",
      completeWhen: "申请人提交完整的定期寿险投保申请",
      completeThen: "申请状态应为“待核保”",
      disclosureScenario: "缺少健康告知时阻止提交",
      disclosureGiven: "必填健康告知答案缺失",
      disclosureWhen: "申请人提交寿险投保申请",
      disclosureThen: "投保申请应显示校验错误",
      highCoverageScenario: "高保额申请需要人工审核",
      highCoverageGiven: "申请保额超过自动核保限额",
      highCoverageWhen: "申请人提交寿险投保申请",
      highCoverageThen: "申请状态应为“需要补充审核”",
    },
    testManagement: {
      heading: "测试管理",
      description: "在同一工作区管理覆盖范围和可复用测试数据。",
      newTestPlan: "新建测试计划",
      testPlan: "测试计划",
      testData: "测试数据",
      searchPlans: "搜索测试计划",
      importedFromAthena: "从 Athena 导入",
      createdManually: "手动创建",
      draft: "草稿",
      importToTestPlan: "导入测试计划",
      importBddAsTestPlan: "将 BDD 导入为测试计划",
      testPlans: "测试计划列表",
      testPlanCount: "个测试计划",
      reusableTestData: "可复用测试数据",
      testDataEmpty: "测试数据集将显示在这里。",
      newDataSet: "新建数据集",
      nameColumn: "名称",
      scenariosColumn: "场景",
      sourceColumn: "来源",
      statusColumn: "状态",
      sections: "测试管理分区",
    },
    lowCode: {
      heading: "寿险投保申请自动化",
      description: "由 Athena 生成 · Playwright · 草稿",
      saveDraft: "保存草稿",
      saved: "已保存",
      automationSteps: "自动化步骤",
      addStep: "添加步骤",
      deleteStep: "删除步骤",
      generatedScript: "生成的脚本",
      openInLowCode: "在低代码自动化中打开",
      updatesWithEveryStep: "随每个步骤更新",
      action: "操作",
      elementOrUrl: "元素或 URL",
      value: "值",
      steps: "个步骤",
      emptyTitle: "还没有自动化草稿",
      emptyDescription:
        "请先让 Athena 生成自动化流程，再把草稿打开到这里编辑步骤。",
      startInAthena: "前往 Athena",
      automationStep: "自动化步骤",
      actionForStep: "步骤操作",
      elementForStep: "步骤元素",
      valueForStep: "步骤值",
      elementPlaceholder: "CSS 选择器、文本或 URL",
      optional: "可选",
      navigate: "导航",
      click: "点击",
      fill: "填写",
      assert: "断言",
      wait: "等待",
    },
  },
} as const satisfies Record<Locale, PrototypeCopy>;
