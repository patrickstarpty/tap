import type { Locale, ProductModule } from "./model";

export interface PrototypeCopy {
  language: {
    en: string;
    zh: string;
  };
  navigation: Record<ProductModule, string> & {
    collapseSidebar: string;
    expandSidebar: string;
    newChat: string;
    chatHistory: string;
    product: string;
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
  };
  sources: {
    heading: string;
    description: string;
    search: string;
    selected: string;
    ready: string;
    loading: string;
    noReadySources: string;
    manageKnowledge: string;
    provenanceHint: string;
    immutableRevision: string;
  };
  composer: {
    addToMessage: string;
    addFromLibrary: string;
    useAgents: string;
    useSkills: string;
    searchLibrary: string;
    searchAgents: string;
    searchSkills: string;
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
  };
  library: {
    heading: string;
    description: string;
    addSource: string;
    thumbnailList: string;
    knowledgeGraph: string;
    sources: string;
    knowledgeGraphImage: string;
    search: string;
    sourceFile: string;
    cancel: string;
    ready: string;
    processing: string;
    failed: string;
    noResults: string;
    illustrative: string;
    application: string;
    underwriting: string;
    healthDisclosure: string;
    beneficiary: string;
    requires: string;
    informs: string;
    names: string;
    supports: string;
  };
  artifacts: {
    bddPlanReady: string;
    automationReady: string;
    scenariosDraft: string;
    automationSummary: string;
    feature: string;
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
    reusableTestData: string;
    testDataEmpty: string;
    newDataSet: string;
    nameColumn: string;
    scenariosColumn: string;
    sourceColumn: string;
    statusColumn: string;
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
      expandSidebar: "Expand sidebar",
      newChat: "New Chat",
      chatHistory: "Chat history",
      product: "Product",
    },
    chat: {
      startConversation: "Start a conversation",
      conversation: "Conversation",
      messageAthena: "Message Athena",
      messageComposer: "Message composer",
      send: "Send",
      placeholder: "Ask Athena anything...",
      heading: "What can I do for you?",
      description:
        "Ask a question, create BDD test cases, or build an automation.",
      sourceHint: "Answers use your selected sources when available.",
      suggestedPrompts: "Suggested prompts",
      quickPrompts: [
        "Summarize the life insurance underwriting rules",
        "Create BDD test cases for life insurance underwriting",
        "Generate an automation script for a life insurance application",
      ],
    },
    sources: {
      heading: "Knowledge sources",
      description: "Choose what Athena can use.",
      search: "Search knowledge sources",
      selected: "selected",
      ready: "Ready",
      loading: "Loading sources",
      noReadySources: "No ready sources",
      manageKnowledge: "Manage knowledge",
      provenanceHint:
        "Answers and generated assets show which selected sources they used.",
      immutableRevision: "immutable revision",
    },
    composer: {
      addToMessage: "Add to message",
      addFromLibrary: "Add from Library",
      useAgents: "Use Agents",
      useSkills: "Use Skills",
      searchLibrary: "Search library",
      searchAgents: "Search agents",
      searchSkills: "Search skills",
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
    },
    library: {
      heading: "Library",
      description:
        "Browse source material and explore its curated domain context.",
      addSource: "Add source",
      thumbnailList: "Thumbnail list",
      knowledgeGraph: "Knowledge Graph",
      sources: "Library sources",
      knowledgeGraphImage: "Life insurance knowledge graph",
      search: "Search library",
      sourceFile: "Source file",
      cancel: "Cancel",
      ready: "Ready",
      processing: "Processing",
      failed: "Failed",
      noResults: "No matching sources",
      illustrative:
        "Illustrative view — relationships are curated for this prototype, not computed.",
      application: "Life insurance application",
      underwriting: "Underwriting",
      healthDisclosure: "Health disclosure",
      beneficiary: "Beneficiary",
      requires: "requires",
      informs: "informs",
      names: "names",
      supports: "supports",
    },
    artifacts: {
      bddPlanReady: "BDD test plan ready",
      automationReady: "Automation draft ready",
      scenariosDraft: "3 scenarios · Draft",
      automationSummary: "BDD scenario + 6 automation steps",
      feature: "Feature: Life insurance application underwriting",
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
      testPlans: "test plans",
      reusableTestData: "Reusable test data",
      testDataEmpty: "Test data sets will appear here.",
      newDataSet: "New data set",
      nameColumn: "Name",
      scenariosColumn: "Scenarios",
      sourceColumn: "Source",
      statusColumn: "Status",
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
      expandSidebar: "展开侧边栏",
      newChat: "新建对话",
      chatHistory: "对话历史",
      product: "产品",
    },
    chat: {
      startConversation: "开始对话",
      conversation: "对话",
      messageAthena: "向 Athena 发送消息",
      messageComposer: "消息编辑器",
      send: "发送",
      placeholder: "向 Athena 提问...",
      heading: "我能为您做什么？",
      description: "提问、创建 BDD 测试用例，或构建自动化流程。",
      sourceHint: "回答会在可用时使用您选择的知识来源。",
      suggestedPrompts: "推荐提示词",
      quickPrompts: [
        "总结寿险新单核保规则",
        "为寿险新单核保创建 BDD 测试用例",
        "为寿险投保申请生成自动化脚本",
      ],
    },
    sources: {
      heading: "知识来源",
      description: "选择 Athena 可以使用的来源。",
      search: "搜索知识来源",
      selected: "已选择",
      ready: "已就绪",
      loading: "正在加载来源",
      noReadySources: "没有可用来源",
      manageKnowledge: "管理知识库",
      provenanceHint: "回答和生成的资产会显示使用了哪些已选来源。",
      immutableRevision: "不可变版本",
    },
    composer: {
      addToMessage: "添加到消息",
      addFromLibrary: "从知识库添加",
      useAgents: "使用智能体",
      useSkills: "使用技能",
      searchLibrary: "搜索知识库",
      searchAgents: "搜索智能体",
      searchSkills: "搜索技能",
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
    },
    library: {
      heading: "知识库",
      description: "浏览知识来源，并探索经过编排的领域上下文。",
      addSource: "添加来源",
      thumbnailList: "缩略图列表",
      knowledgeGraph: "知识图谱",
      sources: "知识库来源",
      knowledgeGraphImage: "寿险知识图谱",
      search: "搜索知识库",
      sourceFile: "来源文件",
      cancel: "取消",
      ready: "已就绪",
      processing: "处理中",
      failed: "失败",
      noResults: "没有匹配的来源",
      illustrative: "示意视图 — 关系由本原型编排，并非计算所得。",
      application: "寿险投保",
      underwriting: "核保",
      healthDisclosure: "健康告知",
      beneficiary: "受益人",
      requires: "需要",
      informs: "影响",
      names: "指定",
      supports: "支持",
    },
    artifacts: {
      bddPlanReady: "BDD 测试计划已就绪",
      automationReady: "自动化草稿已就绪",
      scenariosDraft: "3 个场景 · 草稿",
      automationSummary: "BDD 场景 + 6 个自动化步骤",
      feature: "功能：寿险投保申请核保",
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
      testPlans: "个测试计划",
      reusableTestData: "可复用测试数据",
      testDataEmpty: "测试数据集将显示在这里。",
      newDataSet: "新建数据集",
      nameColumn: "名称",
      scenariosColumn: "场景",
      sourceColumn: "来源",
      statusColumn: "状态",
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
    },
  },
} as const satisfies Record<Locale, PrototypeCopy>;
