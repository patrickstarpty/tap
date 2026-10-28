import type { Locale, ProductModule } from "./model";

export interface WorkspaceCopy {
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
    searchChats: string;
    noMatchingChats: string;
    moreOptions: string;
    renameChat: string;
    deleteChat: string;
    chatName: string;
    chatNameInvalid: string;
    confirmDeleteChat: string;
    deleteChatWarning: string;
    cancel: string;
    historyActionFailed: string;
    product: string;
    tapperTools: string;
    language: string;
    localWorkspace: string;
  };
  chat: {
    startConversation: string;
    conversation: string;
    messageTapper: string;
    messageComposer: string;
    send: string;
    placeholder: string;
    heading: string;
    description: string;
    sourceHint: string;
    promptSuggestions: {
      label: string;
      refresh: string;
      basedOnOne(name: string): string;
      basedOnMany(name: string, total: number): string;
    };
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
    stopGenerating: string;
    sending: string;
    regenerate: string;
    retry: string;
    editQuestion: string;
    answerSummary(
      sourceCount: number,
      chunkCitationCount: number,
      relationCount: number,
    ): string;
    seedEntities(count: number): string;
    relationPaths: string;
    noRelationEvidence: string;
    toolStepRow(
      toolRound: number,
      toolName: string,
      summary: string,
      durationMs: number,
    ): string;
    toolName(toolId: string): string;
    stepStatus: {
      running: string;
      completed: string;
      rejected: string;
      failed: string;
    };
    clarificationLabel: string;
    insightsMetricsHeading: string;
    insightsFactRow(
      label: string,
      metricId: string,
      value: number | null,
      asOf: string,
    ): string;
  };
  sources: {
    heading: string;
    description: string;
    search: string;
    selected: string;
    ready: string;
    loading: string;
    noReadySources: string;
    empty: string;
    pending: string;
    error: string;
    retry: string;
    view: string;
    retryDocument: string;
    deleteSource: string;
    confirmDelete: string;
    cancelDelete: string;
    deleteWarning: string;
    mutationFailed: string;
    documents: string;
    failed: string;
    noResults: string;
    provenanceHint: string;
    immutableRevision: string;
    knowledgeSource: string;
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
    uploadFile: string;
    attachmentUploading: string;
    attachmentProcessing: string;
    attachmentNeedsReview: string;
    attachmentFailed: string;
    reviewInLibrary: string;
    removeAttachment: string;
    fromAgent: string;
    skillBudgetWarning: string;
    skillBudgetExceeded: string;
    largestSkills: string;
    agentModelFallback(agentName: string): string;
    skillDisabledNotice(skillName: string): string;
    skillInapplicableNotice(skillName: string): string;
    /** N-2: `skillById` only ever carries the latest revision of each
     * Skill (C-1), so a `skill_disabled:<id>`/`skill_inapplicable:<id>`
     * notice naming an older bound revision can't be resolved to a name
     * -- these generic (nameless) sentences are the fallback so the raw
     * wire code is never shown to begin with. */
    skillDisabledNoticeGeneric: string;
    skillInapplicableNoticeGeneric: string;
    /** Label/tooltip shown on a greyed-out, unselectable Skill option in
     * the composer picker (spec §4.4) when it doesn't apply to knowledge
     * Q&A. */
    skillNotApplicable: string;
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
    useInChat: string;
    cancel: string;
    noResults: string;
    agentCatalog: string;
    skillCatalog: string;
  };
  skillsWorkspace: {
    heading: string;
    description: string;
    importSkill: string;
    createSkill: string;
    filterAll: string;
    filterBuiltIn: string;
    filterCustom: string;
    filterDisabled: string;
    noResults: string;
    applicableTasks: string;
    revision: string;
    useInChat: string;
    edit: string;
    /** Imported skills aren't offered Edit (I-3): editing would rebuild a
     * SKILL.md-only package and silently drop the original package's
     * references/scripts/assets. This points at re-importing instead. */
    editUnavailableImported: string;
    enable: string;
    disable: string;
    statusEnabled: string;
    statusDisabled: string;
    copyAsCustom: string;
    unavailable: string;
    cancel: string;
    close: string;
    loading: string;
    loadFailed: string;
    retry: string;
    download: string;
    savedNotExecuted: string;
    attachedReferences: string;
    noAttachedReferences: string;
    tabs: {
      instructions: string;
      files: string;
      metadata: string;
      revisions: string;
    };
    taskLabels: {
      "knowledge.answer": string;
      "test-plan.generate": string;
      "automation.generate": string;
    };
    import: {
      step1Title: string;
      step1Description: string;
      browseFiles: string;
      browseFolder: string;
      pasteLabel: string;
      pastePlaceholder: string;
      next: string;
      back: string;
      commit: string;
      step2Title: string;
      frontmatter: string;
      files: string;
      warnings: string;
      category: {
        skill: string;
        referenceAttached: string;
        referenceUnattached: string;
        script: string;
        asset: string;
      };
      nameConflict: string;
      importAsNewRevision: string;
      skip: string;
      stageFailed: string;
      commitFailed: string;
      /** Maps the server's closed error-code vocabulary (I-8) to prose;
       * an unlisted code falls back to `stageFailed`/`commitFailed`. */
      errors: {
        skill_package_too_large: string;
        skill_path_rejected: string;
        skill_frontmatter_invalid: string;
        skill_not_found_in_package: string;
        skill_name_conflict: string;
        revision_conflict: string;
        import_expired: string;
        skill_built_in_immutable: string;
      };
    };
    form: {
      createTitle: string;
      editTitle: string;
      copyTitle: string;
      name: string;
      description: string;
      applicableTasks: string;
      instructions: string;
      preview: string;
      save: string;
      cancel: string;
      nameInvalid: string;
      descriptionRequired: string;
      instructionsRequired: string;
      budgetExceeded: string;
      revisionConflict: string;
      saveFailed: string;
    };
  };
  agentsWorkspace: {
    heading: string;
    description: string;
    createAgent: string;
    filterAll: string;
    filterBuiltIn: string;
    filterCustom: string;
    filterDisabled: string;
    noResults: string;
    useInChat: string;
    edit: string;
    enable: string;
    disable: string;
    upgrade: string;
    close: string;
    revision: string;
    defaultModel: string;
    defaultModelGlobal: string;
    skills: string;
    toolsLabel: string;
    toolsBuiltIn: string;
    hasSkillUpdates: string;
    boundSkillDisabledWarning: string;
    statusEnabled: string;
    statusDisabled: string;
    copyAsCustom: string;
    unavailable: string;
    loading: string;
    loadFailed: string;
    retry: string;
    upgradeFailed: string;
    revisionConflict: string;
    tabs: {
      overview: string;
      skills: string;
      revisions: string;
    };
    form: {
      createTitle: string;
      editTitle: string;
      copyTitle: string;
      name: string;
      description: string;
      systemInstruction: string;
      defaultModel: string;
      defaultModelGlobal: string;
      skills: string;
      searchSkills: string;
      noSkills: string;
      boundSkillDisabled: string;
      /** N-1: shown for a bound Skill whose revision is no longer latest
       * but is still enabled (the ordinary "Skill was revised since
       * binding" case) -- distinct from `boundSkillDisabled` so an
       * outdated-but-enabled binding is never mislabeled as disabled. */
      boundSkillOutdated: string;
      removeSkill: string;
      toolsLabel: string;
      toolsLoadFailed: string;
      save: string;
      cancel: string;
      nameInvalid: string;
      descriptionRequired: string;
      descriptionTooLong: string;
      systemInstructionRequired: string;
      systemInstructionTooLarge: string;
      budgetCalculating: string;
      budgetUnavailable: string;
      budgetWarning: string;
      budgetExceeded: string;
      largestSkills: string;
      nameConflict: string;
      revisionConflict: string;
      saveFailed: string;
    };
  };
  agentTeam: {
    heading: string;
    descriptions: { mine: string; team: string; report: string };
    tablistLabel: string;
    tabs: { mine: string; team: string; report: string; config: string };
    period: {
      label: string;
      last7: string;
      last30: string;
      thisMonth: string;
      lastMonth: string;
      custom: string;
      from: string;
      to: string;
      invalidRange: string;
    };
    metrics: {
      runs: string;
      active: string;
      idle: string;
      agentTotal: string;
      failures: string;
      satisfaction: string;
      latency: string;
      cost: string;
      maintenance: string;
      servedAvg: string;
      ownersAvg: string;
    };
    kpiCaptions: {
      runs: string;
      cost: string;
      latency: string;
      active: string;
    };
    status: { active: string; idle: string; disabled: string };
    platformOwner: string;
    insufficientSample: string;
    runtimeUnavailable: string;
    runtimeSyncing: string;
    runtimeLag: string;
    updatedAt: string;
    pendingFeedbackMine: string;
    markResolved: string;
    transferOwner: string;
    viewConfig: string;
    exportPdf: string;
    exportCsv: string;
    exportFailed: string;
    durationUnits: { ms: string; s: string; m: string; h: string };
    emptyState: string;
    createAgent: string;
    loading: string;
    loadFailed: string;
    retry: string;
    report: {
      title: string;
      project: string;
      period: string;
      summaryHeading: string;
      comparisonHeading: string;
      comparedWith: string;
      trendHeading: string;
      current: string;
      previous: string;
      change: string;
      metric: string;
      failures: string;
      noTrend: string;
    };
    team: {
      viewLabel: string;
      viewCards: string;
      viewTable: string;
      filterOwner: string;
      filterStatus: string;
      filterKind: string;
      allOwners: string;
      allStatuses: string;
      allKinds: string;
      kinds: { custom: string; built_in: string; tapper_default: string };
      rosterHeading: string;
      peopleHeading: string;
      orgChartHeading: string;
      orgChartLabel: string;
      orgChartEmpty: string;
      orgChartOwns: string;
      orgChartServes: string;
      orgChartDimmed: string;
      orgChartUsers: string;
      orgChartHint: string;
      chartHint: string;
      trendHeading: string;
      noJobTitle: string;
      ownerLabel: string;
      runsInPeriod: string;
      runsTrend: string;
      sparklineCaption: string;
      topServed: string;
      noServed: string;
      lastRun: string;
      lastMaintenance: string;
      pendingFeedback: string;
      noPeople: string;
      noMatch: string;
      periodActivity: { column: string; ran: string; noRuns: string };
      columns: {
        name: string;
        jobTitle: string;
        owner: string;
        status: string;
        kind: string;
        runs: string;
        failures: string;
        latencyTotal: string;
        latencyP50: string;
        cost: string;
        served: string;
        lastRun: string;
        satisfaction: string;
        lastMaintenance: string;
        pendingFeedback: string;
        joinedAt: string;
        actor: string;
        ownedAgents: string;
        ownedRuns: string;
        servedByAgents: string;
        calls: string;
        maintenance: string;
      };
    };
    mine: {
      ownedHeading: string;
      servedHeading: string;
      noFeedback: string;
      noOwned: string;
      noServed: string;
      ratingUp: string;
      ratingDown: string;
      ratingNone: string;
    };
    profile: {
      close: string;
      tabs: {
        basic: string;
        workload: string;
        history: string;
        feedback: string;
        settings: string;
      };
      tablistLabel: string;
      noOwner: string;
      servedPeople: string;
      noServed: string;
      currentRevision: string;
      joinedAt: string;
      secondaryMetrics: string;
      primaryMetrics: string;
      generationCalls: string;
      toolCalls: string;
      callsPerRun: string;
      abstained: string;
      canceled: string;
      trendRuns: string;
      trendFailures: string;
      runsUnit: string;
      noHistory: string;
      loadMore: string;
      openRevision: string;
      maintenanceKinds: {
        revised: string;
        skill_upgraded: string;
        disabled: string;
        enabled: string;
        feedback_resolved: string;
        owner_transferred: string;
      };
      noFeedback: string;
      feedbackCount: string;
      resolutionNote: string;
      noteTooLong: string;
      jobTitleLabel: string;
      jobTitleTooLong: string;
      save: string;
      saved: string;
      currentOwner: string;
      newOwnerLabel: string;
      transferred: string;
      transferFailed: string;
      errors: {
        actor_not_found: string;
        agent_read_only: string;
        job_title_too_long: string;
        note_too_long: string;
        feedback_not_found: string;
        generic: string;
      };
    };
  };
  migrationBanner: {
    heading: string;
    descriptionAgent: string;
    descriptionSkill: string;
    import: string;
    dismiss: string;
    importing: string;
    importFailed: string;
  };
  library: {
    listView: string;
    cardView: string;
    preview: string;
    download: string;
    noPreview: string;

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
    concepts: string;
    labeledRelationships: string;
    search: string;
    sourceFile: string;
    cancel: string;
    ready: string;
    processing: string;
    failed: string;
    noResults: string;
    communities: string;
    selectAllTopics: string;
    nodes: string;
    nodeDetails: string;
    searchResults: string;
    noMatchingNodes: string;
    selectNode: string;
    community: string;
    relationships: string;
    provenance: string;
    connections: string;
    zoomIn: string;
    zoomOut: string;
    resetView: string;
    toggleCommunities: string;
    closeNodeDetails: string;
    aliases: string;
    nodeSources: string;
    evidenceSnippets: string;
    openOriginal: string;
    askAboutNode: string;
    showAllRelations(n: number): string;
    relationCount(n: number): string;
    relationIncoming: string;
    relationOutgoing: string;
    nodeDetailsLoading: string;
    nodeDetailsError: string;
    enterFullscreen: string;
    exitFullscreen: string;
    fullscreenUnavailable: string;
    zoomLevel: string;
    extracted: string;
    inferred: string;
    graphNavigationHint: string;
    canvasRegionLabel: string;
    overviewCaption: string;
    highlightedPath: string;
    highlightCaption: string;
    clearHighlight: string;
    versionUpdated: string;
    highlightLoading: string;
    highlightError: string;
    loadMore: string;
    extractingSources(n: number): string;
    partialSources(n: number): string;
    sourceCapNotice(n: number): string;
    graphEmpty: string;
    graphEmptyHint: string;
    graphFailed: string;
    graphFailedHint: string;
    graphMerging: string;
    graphLoading: string;
    graphUnavailable: string;
    otherCommunity: string;
    nodeTypes: Record<
      "ENTITY" | "CONCEPT" | "REQUIREMENT" | "SYSTEM" | "ACTOR" | "PROCESS",
      string
    >;
    relationTypes: Record<
      | "REQUIRES"
      | "APPLIES_TO"
      | "PART_OF"
      | "EXCEPTION_OF"
      | "SUPERSEDES"
      | "TRIGGERS"
      | "PRECEDES"
      | "VALIDATED_BY"
      | "RESPONSIBLE_FOR"
      | "USES"
      | "DEFINES"
      | "CONFLICTS_WITH"
      | "RELATED_TO",
      string
    >;
  };
}

export const WORKSPACE_COPY = {
  en: {
    language: { en: "English", zh: "中文" },
    navigation: {
      tapper: "Tapper",
      agents: "Agents",
      skills: "Skills",
      library: "Library",
      "test-management": "Test Management",
      collapseSidebar: "Collapse sidebar",
      closeSidebar: "Close sidebar",
      expandSidebar: "Expand sidebar",
      newChat: "New chat",
      chatHistory: "Chat history",
      searchChats: "Search chats",
      noMatchingChats: "No matching chats",
      moreOptions: "More options for {title}",
      renameChat: "Rename",
      deleteChat: "Delete",
      chatName: "Chat name",
      chatNameInvalid: "Enter a name of 1–120 characters.",
      confirmDeleteChat: "Delete chat?",
      deleteChatWarning: "This chat will be removed from your history.",
      cancel: "Cancel",
      historyActionFailed: "The chat could not be updated. Try again.",
      product: "Product",
      tapperTools: "Tapper tools",
      language: "Language",
      localWorkspace: "Local workspace",
    },
    chat: {
      startConversation: "Start a conversation",
      conversation: "Conversation",
      messageTapper: "Message Tapper",
      messageComposer: "Message composer",
      send: "Send",
      placeholder: "Ask about life insurance or testing...",
      heading: "What can I do for you?",
      description:
        "Ask about life insurance, create BDD test cases, or build an automation.",
      sourceHint: "Each turn records the knowledge context you select.",
      promptSuggestions: {
        label: "Suggested questions",
        refresh: "Show others",
        basedOnOne: (name: string) => `Based on ${name}`,
        basedOnMany: (name: string, total: number) =>
          `Based on ${name} and ${total - 1} more`,
      },
      noContextNotice: "No knowledge context was selected for this turn.",
      selectedContextNotice:
        "Citations identify the sources that supported this answer.",
      selectedContext: "Selected context",
      assistant: "Tapper assistant",
      questionNavigation: "Questions in this conversation",
      jumpToQuestion: "Jump to question",
      showEarlierQuestion: "Show {count} earlier question",
      showEarlierQuestions: "Show {count} earlier questions",
      showLaterQuestion: "Show {count} later question",
      showLaterQuestions: "Show {count} later questions",
      stopGenerating: "Stop generating",
      sending: "Sending",
      regenerate: "Regenerate",
      retry: "Retry",
      editQuestion: "Edit question",
      answerSummary: (
        sourceCount: number,
        chunkCitationCount: number,
        relationCount: number,
      ) =>
        `Searched ${sourceCount} source${sourceCount === 1 ? "" : "s"} · ${chunkCitationCount} passage${chunkCitationCount === 1 ? "" : "s"} · ${relationCount} relation${relationCount === 1 ? "" : "s"}`,
      seedEntities: (count: number) =>
        `${count} seed entit${count === 1 ? "y" : "ies"}`,
      relationPaths: "Relation paths",
      noRelationEvidence:
        "No direct relation evidence was found; the answer below is grounded in source passages.",
      toolStepRow: (
        toolRound: number,
        toolName: string,
        summary: string,
        durationMs: number,
      ) => `Round ${toolRound} · ${toolName} · ${summary} · ${durationMs} ms`,
      toolName: (toolId: string) =>
        (
          ({
            "knowledge.search": "Knowledge search",
            "graph.relations": "Relation analysis",
            "insights.query": "Insights query",
          }) as Record<string, string>
        )[toolId] ?? toolId,
      stepStatus: {
        running: "Running",
        completed: "Done",
        rejected: "Rejected",
        failed: "Failed",
      },
      clarificationLabel: "Needs more information",
      insightsMetricsHeading: "Insights metrics",
      insightsFactRow: (
        label: string,
        metricId: string,
        value: number | null,
        asOf: string,
      ) => `${label} · ${metricId} = ${value ?? "—"} (as of ${asOf})`,
    },
    sources: {
      heading: "Knowledge sources",
      description: "Choose what Tapper can use.",
      search: "Search knowledge sources",
      selected: "selected",
      ready: "Ready",
      loading: "Loading sources",
      noReadySources: "No ready sources",
      empty: "Upload your first source in Library",
      pending: "Sources are processing",
      error: "Sources could not be loaded",
      retry: "Retry loading",
      view: "View",
      retryDocument: "Retry",
      deleteSource: "Delete source",
      confirmDelete: "Confirm delete",
      cancelDelete: "Cancel",
      deleteWarning:
        "Delete this source and all its documents? It will no longer be available to Tapper.",
      mutationFailed:
        "The source could not be changed. Retry the same action or reload its current state.",
      documents: "documents",
      failed: "failed",
      noResults: "No matching sources",
      provenanceHint:
        "Answers and generated assets record the source context selected for each turn.",
      immutableRevision: "immutable revision",
      knowledgeSource: "Knowledge source",
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
      uploadFile: "Upload file",
      attachmentUploading: "Uploading",
      attachmentProcessing: "Processing",
      attachmentNeedsReview: "No searchable chunks yet",
      attachmentFailed: "Upload failed",
      reviewInLibrary: "Open in Library",
      removeAttachment: "Remove attachment",
      fromAgent: "From Agent",
      skillBudgetWarning:
        "Selected skills are near the per-turn budget. Remove some before they are needed together.",
      skillBudgetExceeded:
        "Selected skills exceed the per-turn budget. Remove some before sending.",
      largestSkills: "Largest skills",
      agentModelFallback: (agentName: string) =>
        `${agentName}'s default model is no longer available; using the global default instead.`,
      skillDisabledNotice: (skillName: string) =>
        `Skill "${skillName}" is disabled and was skipped for this answer.`,
      skillInapplicableNotice: (skillName: string) =>
        `Skill "${skillName}" does not apply to knowledge Q&A and was skipped for this answer.`,
      skillDisabledNoticeGeneric:
        "A selected skill is disabled and was skipped for this answer.",
      skillInapplicableNoticeGeneric:
        "A selected skill does not apply to knowledge Q&A and was skipped for this answer.",
      skillNotApplicable: "Doesn't apply to knowledge Q&A",
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
      useInChat: "Use in chat",
      cancel: "Cancel",
      noResults: "No matching items",
      agentCatalog: "Agent catalog",
      skillCatalog: "Skill catalog",
    },
    skillsWorkspace: {
      heading: "Skills",
      description: "Reusable instructions and files for every conversation.",
      importSkill: "Import skill",
      createSkill: "New skill",
      filterAll: "All",
      filterBuiltIn: "Built-in",
      filterCustom: "Custom",
      filterDisabled: "Disabled",
      noResults: "No skills match this filter.",
      applicableTasks: "Applicable tasks",
      revision: "Revision",
      useInChat: "Use in chat",
      edit: "Edit",
      editUnavailableImported:
        "Imported skills can't be edited in place — import the updated package as a new revision instead.",
      enable: "Enable",
      disable: "Disable",
      statusEnabled: "Enabled",
      statusDisabled: "Disabled",
      copyAsCustom: "Copy as custom",
      unavailable: "Skills are not available for this chat yet.",
      cancel: "Cancel",
      close: "Close",
      loading: "Loading…",
      loadFailed: "This skill could not be loaded.",
      retry: "Retry",
      download: "Download",
      savedNotExecuted: "Saved, not executed",
      attachedReferences: "Attached reference text",
      noAttachedReferences: "No reference text was attached.",
      tabs: {
        instructions: "Instructions",
        files: "Files",
        metadata: "Metadata",
        revisions: "Revision history",
      },
      taskLabels: {
        "knowledge.answer": "Knowledge answers",
        "test-plan.generate": "Test plan generation",
        "automation.generate": "Automation generation",
      },
      import: {
        step1Title: "Import a skill",
        step1Description:
          "Drop a SKILL.md file, a zip archive or a folder, or paste the file content below.",
        browseFiles: "Browse files",
        browseFolder: "Browse folder",
        pasteLabel: "Paste SKILL.md content",
        pastePlaceholder: "Paste the SKILL.md content here",
        next: "Preview",
        back: "Back",
        commit: "Import selected",
        step2Title: "Review before import",
        frontmatter: "Frontmatter",
        files: "Files",
        warnings: "Warnings",
        category: {
          skill: "Instructions",
          referenceAttached: "Reference · attached",
          referenceUnattached: "Reference · not attached",
          script: "Script · saved, not executed",
          asset: "Asset",
        },
        nameConflict: "A skill with this name already exists.",
        importAsNewRevision: "Import as a new revision",
        skip: "Skip",
        stageFailed: "This import could not be read.",
        commitFailed: "The selected skills could not be imported.",
        errors: {
          skill_package_too_large: "This package is over the 20 MB limit.",
          skill_path_rejected:
            'This package contains an unsafe path (absolute path, "..", or a symlink).',
          skill_frontmatter_invalid:
            "SKILL.md's frontmatter is missing a required field or is invalid.",
          skill_not_found_in_package:
            "No SKILL.md was found at the selected location in this package.",
          skill_name_conflict:
            'A skill with this name already exists. Choose "Import as a new revision" or skip it.',
          revision_conflict:
            "This skill was updated elsewhere. Go back and import again.",
          import_expired:
            "This staged import has expired. Start the import again.",
          skill_built_in_immutable:
            "Built-in skills cannot receive an imported revision.",
        },
      },
      form: {
        createTitle: "New skill",
        editTitle: "Edit skill",
        copyTitle: "Copy as a custom skill",
        name: "Name",
        description: "Description",
        applicableTasks: "Applicable tasks",
        instructions: "Instructions",
        preview: "SKILL.md preview",
        save: "Save",
        cancel: "Cancel",
        nameInvalid: "Enter a lowercase, hyphenated name up to 64 characters.",
        descriptionRequired: "Enter a description.",
        instructionsRequired: "Enter instructions.",
        budgetExceeded: "SKILL.md is over the 256 KB limit.",
        revisionConflict: "This content was updated elsewhere. Please refresh.",
        saveFailed: "This skill could not be saved.",
      },
    },
    agentsWorkspace: {
      heading: "Agents",
      description:
        "Focused collaborators with a system prompt, a default model and bundled skills.",
      createAgent: "New agent",
      filterAll: "All",
      filterBuiltIn: "Built-in",
      filterCustom: "Custom",
      filterDisabled: "Disabled",
      noResults: "No agents match this filter.",
      useInChat: "Use in chat",
      edit: "Edit",
      enable: "Enable",
      disable: "Disable",
      upgrade: "Upgrade",
      close: "Close",
      revision: "Revision",
      defaultModel: "Default model",
      defaultModelGlobal: "Use the global default model",
      skills: "Skills",
      toolsLabel: "Built-in tools",
      toolsBuiltIn: "Knowledge search, Knowledge answer (always enabled)",
      hasSkillUpdates: "A bundled skill has a newer revision.",
      boundSkillDisabledWarning:
        "A bundled skill has been disabled and will be skipped when this agent answers.",
      statusEnabled: "Enabled",
      statusDisabled: "Disabled",
      copyAsCustom: "Copy as custom",
      unavailable: "Agents are not available for this chat yet.",
      loading: "Loading agents…",
      loadFailed: "Agents could not be loaded.",
      retry: "Retry",
      upgradeFailed: "This agent could not be upgraded.",
      revisionConflict: "This content was updated elsewhere. Please refresh.",
      tabs: {
        overview: "Overview",
        skills: "Skills",
        revisions: "Revision history",
      },
      form: {
        createTitle: "New agent",
        editTitle: "Edit agent",
        copyTitle: "Copy as a custom agent",
        name: "Name",
        description: "Description",
        systemInstruction: "System instruction",
        defaultModel: "Default model",
        defaultModelGlobal: "Use the global default model",
        skills: "Skills",
        searchSkills: "Search skills",
        noSkills: "No enabled skills available.",
        boundSkillDisabled:
          "This bound Skill has been disabled and will be skipped when the Agent answers.",
        boundSkillOutdated:
          "This bound Skill has a newer revision; the Agent still uses the revision it was bound to.",
        removeSkill: "Remove",
        toolsLabel: "Tools",
        toolsLoadFailed: "Tools could not be loaded.",
        save: "Save",
        cancel: "Cancel",
        nameInvalid: "Enter a name up to 128 characters.",
        descriptionRequired: "Enter a description.",
        descriptionTooLong: "Description is over the 1,024 character limit.",
        systemInstructionRequired: "Enter a system instruction.",
        systemInstructionTooLarge:
          "System instruction is over the 32 KB limit.",
        budgetCalculating: "Calculating the Skill budget…",
        budgetUnavailable:
          "The Skill budget could not be calculated. Try again.",
        budgetWarning:
          "Selected skills are near the per-turn budget. Remove some before they are needed together.",
        budgetExceeded:
          "Selected skills exceed the per-turn budget. Remove some before saving.",
        largestSkills: "Largest skills",
        nameConflict: "An agent with this name already exists.",
        revisionConflict: "This content was updated elsewhere. Please refresh.",
        saveFailed: "This agent could not be saved.",
      },
    },
    agentTeam: {
      heading: "Agent team",
      descriptions: {
        mine: "The Agents you own, the Agents that serve you, and feedback waiting for you.",
        team: "Who is on the Agent team, what each member has done and who looks after them.",
        report:
          "A printable summary of the selected period compared with the one before.",
      },
      tablistLabel: "Agents views",
      tabs: {
        mine: "My Agents",
        team: "Team",
        report: "Report",
        config: "Configuration",
      },
      period: {
        label: "Period",
        last7: "Last 7 days",
        last30: "Last 30 days",
        thisMonth: "This month",
        lastMonth: "Last month",
        custom: "Custom",
        from: "From",
        to: "To",
        invalidRange: "Start date must be on or before end date",
      },
      metrics: {
        runs: "Runs",
        active: "Active",
        idle: "Idle",
        agentTotal: "Agents",
        failures: "Failures",
        satisfaction: "Satisfaction",
        latency: "Total latency",
        cost: "Cost",
        maintenance: "Maintenance actions",
        servedAvg: "Agents used per caller",
        ownersAvg: "Agents per owner",
      },
      kpiCaptions: {
        runs: "Runs in the selected period",
        cost: "Model spend in the selected period",
        latency: "Time spent across all runs",
        active: "Ran in the period · {total} Agents in total",
      },
      status: { active: "Active", idle: "Idle", disabled: "Disabled" },
      platformOwner: "Platform-maintained",
      insufficientSample: "Not enough ratings",
      runtimeUnavailable: "Run data is temporarily unavailable",
      runtimeSyncing: "Run data is syncing",
      runtimeLag: "Run data can trail conversations by one sync cycle",
      updatedAt: "Data updated at {time} ({timezone})",
      pendingFeedbackMine: "Feedback waiting for me",
      markResolved: "Mark as resolved",
      transferOwner: "Transfer owner",
      viewConfig: "View configuration",
      exportPdf: "Export PDF",
      exportCsv: "Export CSV",
      exportFailed: "The CSV export failed. Please try again.",
      durationUnits: { ms: "ms", s: "s", m: "m", h: "h" },
      emptyState: "No Agents yet. Create one",
      createAgent: "Create Agent",
      loading: "Loading team data...",
      loadFailed: "Team data could not be loaded.",
      retry: "Retry",
      report: {
        title: "Agent team report",
        project: "Project",
        period: "Period",
        summaryHeading: "Summary",
        comparisonHeading: "Compared with the previous period",
        comparedWith: "Compared with {from} – {to}",
        trendHeading: "Run trend",
        current: "This period",
        previous: "Previous period",
        change: "Change",
        metric: "Metric",
        failures: "Failures",
        noTrend: "No run trend in this period",
      },
      team: {
        viewLabel: "Roster view",
        viewCards: "Cards",
        viewTable: "Table",
        filterOwner: "Owner",
        filterStatus: "Status",
        filterKind: "Type",
        allOwners: "All owners",
        allStatuses: "All statuses",
        allKinds: "All types",
        kinds: {
          custom: "Custom",
          built_in: "Built-in",
          tapper_default: "Tapper default",
        },
        rosterHeading: "Roster",
        peopleHeading: "People",
        orgChartHeading: "Org chart",
        orgChartLabel:
          "Org chart of people and the agents they own or are served by",
        orgChartEmpty: "No ownership or usage relations in this period.",
        orgChartOwns: "Owns",
        orgChartServes: "Served by",
        orgChartDimmed: "Not related to the selected person",
        orgChartUsers: "Other people",
        orgChartHint: "Select a person to see the Agents that serve them.",
        chartHint: "Use the left and right arrow keys to read each point.",
        trendHeading: "Run trend",
        noJobTitle: "No job title",
        ownerLabel: "Owner",
        runsInPeriod: "Runs in period",
        runsTrend: "Runs trend, last 30 days",
        sparklineCaption: "Last 30 days",
        topServed: "Top served",
        noServed: "No one yet",
        lastRun: "Last run",
        lastMaintenance: "Last maintenance",
        pendingFeedback: "Pending feedback",
        noPeople: "No people in this period",
        noMatch: "No matching Agents",
        periodActivity: {
          column: "This period",
          ran: "Ran this period",
          noRuns: "No runs this period",
        },
        columns: {
          name: "Name",
          jobTitle: "Job title",
          owner: "Owner",
          status: "Status",
          kind: "Type",
          runs: "Runs",
          failures: "Failures",
          latencyTotal: "Total latency",
          latencyP50: "Median latency",
          cost: "Cost",
          served: "People served",
          lastRun: "Last run",
          satisfaction: "Satisfaction",
          lastMaintenance: "Last maintenance",
          pendingFeedback: "Pending feedback",
          joinedAt: "Joined",
          actor: "Person",
          ownedAgents: "Agents owned",
          ownedRuns: "Runs of owned Agents",
          servedByAgents: "Agents used",
          calls: "Calls made",
          maintenance: "Maintenance actions",
        },
      },
      mine: {
        ownedHeading: "Agents I own",
        servedHeading: "Agents that serve me",
        noFeedback: "No feedback waiting for you",
        noOwned: "You do not own any Agents yet",
        noServed: "No Agent has served you in this period",
        ratingUp: "Helpful",
        ratingDown: "Not helpful",
        ratingNone: "Comment only",
      },
      profile: {
        close: "Close",
        tabs: {
          basic: "Basics",
          workload: "Workload",
          history: "History",
          feedback: "Pending feedback",
          settings: "Settings",
        },
        tablistLabel: "Agent profile sections",
        noOwner: "No owner",
        servedPeople: "People served",
        noServed: "No one served in this period",
        currentRevision: "Current revision",
        joinedAt: "Joined",
        secondaryMetrics: "Call volume",
        primaryMetrics: "Run metrics",
        generationCalls: "Model calls",
        toolCalls: "Tool calls",
        callsPerRun: "Calls per run",
        abstained: "Abstained",
        canceled: "Canceled",
        trendRuns: "Runs",
        trendFailures: "Failures",
        runsUnit: "runs",
        noHistory: "No maintenance history yet.",
        loadMore: "Load more",
        openRevision: "Open revision",
        maintenanceKinds: {
          revised: "Revised",
          skill_upgraded: "Skill upgraded",
          disabled: "Disabled",
          enabled: "Enabled",
          feedback_resolved: "Feedback resolved",
          owner_transferred: "Owner transferred",
        },
        noFeedback: "No pending feedback.",
        feedbackCount: "Pending feedback: {count}",
        resolutionNote: "Resolution note (optional, up to 500 characters)",
        noteTooLong: "The note is over 500 characters.",
        jobTitleLabel: "Job title",
        jobTitleTooLong: "The job title is over 64 characters.",
        save: "Save",
        saved: "Saved",
        currentOwner: "Current owner",
        newOwnerLabel: "New owner",
        transferred: "Transferred to {owner}",
        transferFailed: "The owner could not be transferred.",
        errors: {
          actor_not_found: "That person was not found in this Project.",
          agent_read_only: "This Agent is read-only.",
          job_title_too_long: "The job title is over 64 characters.",
          note_too_long: "The note is over 500 characters.",
          feedback_not_found: "That feedback no longer exists.",
          generic: "The change could not be saved.",
        },
      },
    },
    migrationBanner: {
      heading: "Local drafts found",
      descriptionAgent:
        "Markdown agent drafts saved on this device can be imported as real agents.",
      descriptionSkill:
        "Markdown skill drafts saved on this device can be imported as real skills.",
      import: "Import",
      dismiss: "Dismiss",
      importing: "Importing…",
      importFailed: "This draft could not be imported.",
    },
    library: {
      listView: "List view",
      cardView: "Card view",
      preview: "Preview",
      download: "Download file",
      noPreview: "No preview available for this source.",
      heading: "Library",
      description:
        "Browse source material and explore its curated domain context.",
      addSource: "Add source",
      all: "Documents",
      knowledgeGraph: "Knowledge Graph",
      sources: "Library sources",
      sourceCount: "sources",
      typeFilter: "Type",
      statusFilter: "Status",
      allTypes: "All types",
      allStatuses: "All statuses",
      clearFilters: "Clear filters",
      knowledgeGraphImage: "Project knowledge graph",
      graphSummary: "Knowledge graph summary",
      concepts: "Concepts",
      labeledRelationships: "Labeled relationships",
      search: "Search library",
      sourceFile: "Source file",
      cancel: "Cancel",
      ready: "Ready",
      processing: "Processing",
      failed: "Failed",
      noResults: "No matching sources",
      communities: "Topic groups",
      selectAllTopics: "Select all",
      nodes: "nodes",
      nodeDetails: "Node details",
      searchResults: "Search results",
      noMatchingNodes:
        "No matching documents, concepts or entities. Try another keyword or clear the filters.",
      selectNode: "Select a node to inspect its relationships.",
      community: "Topic group",
      relationships: "Relationships",
      provenance: "Provenance",
      connections: "connections",
      zoomIn: "Zoom in",
      zoomOut: "Zoom out",
      resetView: "Reset view",
      toggleCommunities: "Toggle topic groups",
      closeNodeDetails: "Close node details",
      aliases: "Aliases",
      nodeSources: "Sources",
      evidenceSnippets: "Evidence",
      openOriginal: "Open original",
      askAboutNode: "Ask about this",
      showAllRelations: (n: number) => `Show all (${n})`,
      relationCount: (n: number) => `${n} relation${n === 1 ? "" : "s"}`,
      relationIncoming: "Incoming",
      relationOutgoing: "Outgoing",
      highlightedPath: "Highlighted path",
      highlightCaption: "Relations cited by the answer",
      clearHighlight: "Back to overview",
      versionUpdated:
        "The graph has been updated; some relations are no longer available.",
      highlightLoading: "Loading the highlighted path…",
      highlightError: "The highlighted path is temporarily unavailable.",
      nodeDetailsLoading: "Loading node details…",
      nodeDetailsError: "Node details are unavailable. Try again.",
      enterFullscreen: "Enter fullscreen",
      exitFullscreen: "Exit fullscreen",
      fullscreenUnavailable: "Fullscreen is unavailable in this browser.",
      zoomLevel: "Zoom level",
      extracted: "EXTRACTED",
      inferred: "INFERRED",
      graphNavigationHint:
        "Drag to pan, use the controls to zoom, and select a node to inspect its relationships.",
      canvasRegionLabel: "Knowledge graph canvas",
      overviewCaption: "Project knowledge overview",
      loadMore: "Load more",
      extractingSources: (n: number) =>
        `${n} source${n === 1 ? "" : "s"} still extracting`,
      partialSources: (n: number) => `${n} partially failed`,
      sourceCapNotice: (n: number) =>
        `Showing the graph for the first 50 of ${n} matching sources.`,
      graphEmpty: "No knowledge graph yet.",
      graphEmptyHint:
        "The graph is built automatically after sources are published.",
      graphFailed: "The knowledge graph build failed.",
      graphFailedHint: "Retry the build, or check with an operator.",
      graphMerging: "The graph is being rebuilt…",
      graphLoading: "Loading the knowledge graph…",
      graphUnavailable:
        "The knowledge graph is temporarily unavailable. Try again.",
      otherCommunity: "Other",
      nodeTypes: {
        ENTITY: "Entity",
        CONCEPT: "Concept",
        REQUIREMENT: "Requirement",
        SYSTEM: "System",
        ACTOR: "Actor",
        PROCESS: "Process",
      },
      relationTypes: {
        REQUIRES: "Requires",
        APPLIES_TO: "Applies to",
        PART_OF: "Part of",
        EXCEPTION_OF: "Exception of",
        SUPERSEDES: "Supersedes",
        TRIGGERS: "Triggers",
        PRECEDES: "Precedes",
        VALIDATED_BY: "Validated by",
        RESPONSIBLE_FOR: "Responsible for",
        USES: "Uses",
        DEFINES: "Defines",
        CONFLICTS_WITH: "Conflicts with",
        RELATED_TO: "Related to",
      },
    },
  },
  zh: {
    language: { en: "English", zh: "中文" },
    navigation: {
      tapper: "Tapper",
      agents: "智能体",
      skills: "技能",
      library: "知识库",
      "test-management": "测试管理",
      collapseSidebar: "收起侧边栏",
      closeSidebar: "关闭侧边栏",
      expandSidebar: "展开侧边栏",
      newChat: "新建对话",
      chatHistory: "对话历史",
      searchChats: "搜索对话",
      noMatchingChats: "没有匹配的对话",
      moreOptions: "更多操作：{title}",
      renameChat: "重命名",
      deleteChat: "删除",
      chatName: "对话名称",
      chatNameInvalid: "请输入 1–120 个字符的名称。",
      confirmDeleteChat: "删除对话？",
      deleteChatWarning: "该对话将从历史中移除。",
      cancel: "取消",
      historyActionFailed: "对话未能更新，请重试。",
      product: "产品",
      tapperTools: "Tapper 工具",
      language: "语言",
      localWorkspace: "本地工作区",
    },
    chat: {
      startConversation: "开始对话",
      conversation: "对话",
      messageTapper: "向 Tapper 发送消息",
      messageComposer: "消息编辑器",
      send: "发送",
      placeholder: "询问寿险业务或测试问题...",
      heading: "我能为您做什么？",
      description: "询问寿险业务、创建 BDD 测试用例，或构建自动化流程。",
      sourceHint: "每轮对话都会记录您选择的知识上下文。",
      promptSuggestions: {
        label: "推荐问题",
        refresh: "换一批",
        basedOnOne: (name: string) => `基于《${name}》`,
        basedOnMany: (name: string, total: number) =>
          `基于《${name}》等 ${total} 份资料`,
      },
      noContextNotice: "此轮对话未选择知识上下文。回答仅基于当前可用信息。",
      selectedContextNotice: "引用会标明支持本次回答的知识来源。",
      selectedContext: "已选上下文",
      assistant: "Tapper 助手",
      questionNavigation: "本次对话中的问题",
      jumpToQuestion: "跳转到问题",
      showEarlierQuestion: "显示上方另外 {count} 个问题",
      showEarlierQuestions: "显示上方另外 {count} 个问题",
      showLaterQuestion: "显示下方另外 {count} 个问题",
      showLaterQuestions: "显示下方另外 {count} 个问题",
      stopGenerating: "停止生成",
      sending: "正在发送",
      regenerate: "重新生成",
      retry: "重试",
      editQuestion: "编辑问题",
      answerSummary: (
        sourceCount: number,
        chunkCitationCount: number,
        relationCount: number,
      ) =>
        `搜索 ${sourceCount} 个来源 · ${chunkCitationCount} 段原文 · ${relationCount} 条关系`,
      seedEntities: (count: number) => `${count} 个种子实体`,
      relationPaths: "关系路径",
      noRelationEvidence: "未找到直接关系证据，以下为资料原文依据",
      toolStepRow: (
        toolRound: number,
        toolName: string,
        summary: string,
        durationMs: number,
      ) => `第 ${toolRound} 轮 · ${toolName} · ${summary} · ${durationMs} ms`,
      toolName: (toolId: string) =>
        (
          ({
            "knowledge.search": "知识检索",
            "graph.relations": "关系分析",
            "insights.query": "Insights 查询",
          }) as Record<string, string>
        )[toolId] ?? toolId,
      stepStatus: {
        running: "运行中",
        completed: "完成",
        rejected: "已拒绝",
        failed: "失败",
      },
      clarificationLabel: "需要补充信息",
      insightsMetricsHeading: "Insights 指标",
      insightsFactRow: (
        label: string,
        metricId: string,
        value: number | null,
        asOf: string,
      ) => `${label} · ${metricId} = ${value ?? "—"}（截至 ${asOf}）`,
    },
    sources: {
      heading: "知识来源",
      description: "选择 Tapper 可以使用的来源。",
      search: "搜索知识来源",
      selected: "已选择",
      ready: "已就绪",
      loading: "正在加载来源",
      noReadySources: "没有可用来源",
      empty: "在知识库上传首个来源",
      pending: "来源正在处理中",
      error: "无法加载来源",
      retry: "重试加载",
      view: "查看",
      retryDocument: "重试",
      deleteSource: "删除来源",
      confirmDelete: "确认删除",
      cancelDelete: "取消",
      deleteWarning: "删除此来源及其全部文档？Tapper 将无法再使用此来源。",
      mutationFailed: "无法更改来源。请重试同一操作或重新加载当前状态。",
      documents: "文档",
      failed: "失败",
      noResults: "没有匹配的来源",
      provenanceHint: "回答和生成的资产会记录每轮对话选择的来源上下文。",
      immutableRevision: "不可变版本",
      knowledgeSource: "知识来源",
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
      uploadFile: "上传文件",
      attachmentUploading: "正在上传",
      attachmentProcessing: "正在处理",
      attachmentNeedsReview: "暂无可检索切片",
      attachmentFailed: "上传失败",
      reviewInLibrary: "在知识库中打开",
      removeAttachment: "移除附件",
      fromAgent: "来自 Agent",
      skillBudgetWarning: "所选 Skill 已接近单轮预算上限，请考虑精简组合。",
      skillBudgetExceeded:
        "所选 Skill 已超出单轮预算上限，请先移除部分 Skill 再发送。",
      largestSkills: "占用最大的 Skill",
      agentModelFallback: (agentName: string) =>
        `${agentName} 的默认模型已不可用，已改用全局默认模型。`,
      skillDisabledNotice: (skillName: string) =>
        `Skill「${skillName}」已停用，本次回答未使用。`,
      skillInapplicableNotice: (skillName: string) =>
        `Skill「${skillName}」不适用于知识问答，本次回答未使用。`,
      skillDisabledNoticeGeneric: "有一个选用的 Skill 已停用，本次回答未使用。",
      skillInapplicableNoticeGeneric:
        "有一个选用的 Skill 不适用于知识问答，本次回答未使用。",
      skillNotApplicable: "不适用于知识问答",
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
      useInChat: "在对话中使用",
      cancel: "取消",
      noResults: "没有匹配项",
      agentCatalog: "智能体目录",
      skillCatalog: "技能目录",
    },
    skillsWorkspace: {
      heading: "技能",
      description: "可在每次对话中复用的指令与文件。",
      importSkill: "导入 Skill",
      createSkill: "新建 Skill",
      filterAll: "全部",
      filterBuiltIn: "内置",
      filterCustom: "自定义",
      filterDisabled: "已停用",
      noResults: "没有符合筛选条件的 Skill。",
      applicableTasks: "适用任务",
      revision: "修订",
      useInChat: "在对话中使用",
      edit: "编辑",
      editUnavailableImported:
        "导入的 Skill 不能直接编辑，请将更新后的包作为新修订重新导入。",
      enable: "启用",
      disable: "停用",
      statusEnabled: "已启用",
      statusDisabled: "已停用",
      copyAsCustom: "复制为自定义",
      unavailable: "本次对话暂不支持使用 Skill。",
      cancel: "取消",
      close: "关闭",
      loading: "加载中…",
      loadFailed: "无法加载该 Skill。",
      retry: "重试",
      download: "下载",
      savedNotExecuted: "已保存，不执行",
      attachedReferences: "已附加的参考文本",
      noAttachedReferences: "未附加参考文本。",
      tabs: {
        instructions: "指令",
        files: "文件",
        metadata: "元数据",
        revisions: "修订历史",
      },
      taskLabels: {
        "knowledge.answer": "知识问答",
        "test-plan.generate": "测试计划生成",
        "automation.generate": "自动化生成",
      },
      import: {
        step1Title: "导入 Skill",
        step1Description:
          "拖入 SKILL.md、zip 压缩包或文件夹，或在下方粘贴文本。",
        browseFiles: "选择文件",
        browseFolder: "选择文件夹",
        pasteLabel: "粘贴 SKILL.md 内容",
        pastePlaceholder: "在此粘贴 SKILL.md 内容",
        next: "预览",
        back: "返回",
        commit: "导入所选",
        step2Title: "导入前确认",
        frontmatter: "元数据",
        files: "文件",
        warnings: "告警",
        category: {
          skill: "指令",
          referenceAttached: "参考·已附加",
          referenceUnattached: "参考·未附加",
          script: "脚本·已保存不执行",
          asset: "资源",
        },
        nameConflict: "已存在同名 Skill。",
        importAsNewRevision: "作为新修订导入",
        skip: "跳过",
        stageFailed: "无法读取此次导入。",
        commitFailed: "无法导入所选 Skill。",
        errors: {
          skill_package_too_large: "压缩包超过 20 MB 上限。",
          skill_path_rejected:
            "压缩包内含不安全路径（绝对路径、“..” 或符号链接）。",
          skill_frontmatter_invalid:
            "SKILL.md 的 frontmatter 缺少必需字段或不合法。",
          skill_not_found_in_package: "在该位置未找到 SKILL.md。",
          skill_name_conflict:
            "已存在同名 Skill，请选择“作为新修订导入”或跳过。",
          revision_conflict: "该 Skill 已在别处更新，请返回重新导入。",
          import_expired: "此次暂存的导入已过期，请重新导入。",
          skill_built_in_immutable: "内置 Skill 不能接收导入的修订。",
        },
      },
      form: {
        createTitle: "新建 Skill",
        editTitle: "编辑 Skill",
        copyTitle: "复制为自定义 Skill",
        name: "名称",
        description: "描述",
        applicableTasks: "适用任务",
        instructions: "指令",
        preview: "SKILL.md 预览",
        save: "保存",
        cancel: "取消",
        nameInvalid: "请输入小写连字符名称，最多 64 个字符。",
        descriptionRequired: "请填写描述。",
        instructionsRequired: "请填写指令。",
        budgetExceeded: "SKILL.md 超过 256 KB 限制。",
        revisionConflict: "内容已被他人更新，请刷新。",
        saveFailed: "无法保存该 Skill。",
      },
    },
    agentsWorkspace: {
      heading: "智能体",
      description: "带有系统提示词、默认模型与已绑定 Skill 的专注协作者。",
      createAgent: "新建 Agent",
      filterAll: "全部",
      filterBuiltIn: "内置",
      filterCustom: "自定义",
      filterDisabled: "已停用",
      noResults: "没有符合筛选条件的 Agent。",
      useInChat: "在对话中使用",
      edit: "编辑",
      enable: "启用",
      disable: "停用",
      upgrade: "升级",
      close: "关闭",
      revision: "修订",
      defaultModel: "默认模型",
      defaultModelGlobal: "使用全局默认模型",
      skills: "Skill",
      toolsLabel: "内置工具",
      toolsBuiltIn: "知识检索、知识回答（始终启用）",
      hasSkillUpdates: "已绑定的 Skill 有新修订。",
      boundSkillDisabledWarning: "已绑定的 Skill 已停用，回答时将跳过。",
      statusEnabled: "已启用",
      statusDisabled: "已停用",
      copyAsCustom: "复制为自定义",
      unavailable: "本次对话暂不支持使用 Agent。",
      loading: "正在加载 Agent…",
      loadFailed: "无法加载 Agent。",
      retry: "重试",
      upgradeFailed: "无法升级该 Agent。",
      revisionConflict: "内容已被他人更新，请刷新。",
      tabs: {
        overview: "概览",
        skills: "Skill",
        revisions: "修订历史",
      },
      form: {
        createTitle: "新建 Agent",
        editTitle: "编辑 Agent",
        copyTitle: "复制为自定义 Agent",
        name: "名称",
        description: "描述",
        systemInstruction: "系统提示词",
        defaultModel: "默认模型",
        defaultModelGlobal: "使用全局默认模型",
        skills: "Skill",
        searchSkills: "搜索 Skill",
        noSkills: "暂无可用的已启用 Skill。",
        boundSkillDisabled: "此绑定的 Skill 已停用，回答时将跳过。",
        boundSkillOutdated:
          "此绑定的 Skill 有新修订，Agent 仍使用绑定时的修订。",
        removeSkill: "移除",
        toolsLabel: "工具",
        toolsLoadFailed: "工具加载失败。",
        save: "保存",
        cancel: "取消",
        nameInvalid: "请输入不超过 128 个字符的名称。",
        descriptionRequired: "请填写描述。",
        descriptionTooLong: "描述超过 1024 个字符限制。",
        systemInstructionRequired: "请填写系统提示词。",
        systemInstructionTooLarge: "系统提示词超过 32 KB 限制。",
        budgetCalculating: "正在计算 Skill 预算…",
        budgetUnavailable: "无法计算 Skill 预算，请重试。",
        budgetWarning: "所选 Skill 已接近单轮预算上限，请考虑精简组合。",
        budgetExceeded:
          "所选 Skill 已超出单轮预算上限，请先移除部分 Skill 再保存。",
        largestSkills: "占用最大的 Skill",
        nameConflict: "已存在同名 Agent。",
        revisionConflict: "内容已被他人更新，请刷新。",
        saveFailed: "无法保存该 Agent。",
      },
    },
    agentTeam: {
      heading: "Agent 团队",
      descriptions: {
        mine: "你负责的 Agent、服务过你的 Agent，以及等你处理的反馈。",
        team: "团队里有哪些 Agent、各自做了什么，以及由谁负责。",
        report: "所选周期与上一周期对比的可打印汇总。",
      },
      tablistLabel: "Agent 视图",
      tabs: {
        mine: "我的 Agent",
        team: "团队",
        report: "汇报",
        config: "配置",
      },
      period: {
        label: "周期",
        last7: "近 7 天",
        last30: "近 30 天",
        thisMonth: "本月",
        lastMonth: "上月",
        custom: "自定义",
        from: "开始",
        to: "结束",
        invalidRange: "开始日期不能晚于结束日期",
      },
      metrics: {
        runs: "运行次数",
        active: "活跃",
        idle: "闲置",
        agentTotal: "Agent 数",
        failures: "失败",
        satisfaction: "满意度",
        latency: "总耗时",
        cost: "成本",
        maintenance: "维护动作",
        servedAvg: "每位使用者用到的 Agent 数",
        ownersAvg: "每位负责人管理 Agent 数",
      },
      kpiCaptions: {
        runs: "所选周期内的运行总数",
        cost: "所选周期内的模型费用",
        latency: "所有运行的耗时之和",
        active: "周期内有运行 · 共 {total} 个 Agent",
      },
      status: { active: "活跃", idle: "闲置", disabled: "已停用" },
      platformOwner: "平台维护",
      insufficientSample: "样本不足",
      runtimeUnavailable: "运行数据暂不可用",
      runtimeSyncing: "运行数据同步中",
      runtimeLag: "运行数据可能比对话晚一个同步周期",
      updatedAt: "数据更新于 {time}（{timezone}）",
      pendingFeedbackMine: "待我处理的反馈",
      markResolved: "标记已处理",
      transferOwner: "转交负责人",
      viewConfig: "查看配置",
      exportPdf: "导出 PDF",
      exportCsv: "导出 CSV",
      exportFailed: "导出 CSV 失败，请重试。",
      durationUnits: { ms: "毫秒", s: "秒", m: "分", h: "小时" },
      emptyState: "还没有 Agent，新建一个",
      createAgent: "新建 Agent",
      loading: "正在加载团队数据…",
      loadFailed: "无法加载团队数据。",
      retry: "重试",
      report: {
        title: "Agent 团队汇报",
        project: "项目",
        period: "周期",
        summaryHeading: "汇总",
        comparisonHeading: "与上一周期对比",
        comparedWith: "对比：{from} – {to}",
        trendHeading: "运行趋势",
        current: "本周期",
        previous: "上一周期",
        change: "变化",
        metric: "指标",
        failures: "失败",
        noTrend: "本周期没有运行趋势",
      },
      team: {
        viewLabel: "花名册视图",
        viewCards: "卡片",
        viewTable: "表格",
        filterOwner: "负责人",
        filterStatus: "状态",
        filterKind: "类型",
        allOwners: "全部负责人",
        allStatuses: "全部状态",
        allKinds: "全部类型",
        kinds: {
          custom: "自定义",
          built_in: "内置",
          tapper_default: "Tapper 默认",
        },
        rosterHeading: "花名册",
        peopleHeading: "人员",
        orgChartHeading: "组织架构图",
        orgChartLabel: "人员与其负责、使用的 Agent 的组织架构图",
        orgChartEmpty: "本周期内没有负责或使用关系。",
        orgChartOwns: "负责",
        orgChartServes: "使用",
        orgChartDimmed: "与所选成员无关",
        orgChartUsers: "其他使用者",
        orgChartHint: "选择一位人员，查看为其服务的 Agent。",
        chartHint: "用左右方向键逐点查看数值。",
        trendHeading: "运行趋势",
        noJobTitle: "未设岗位",
        ownerLabel: "负责人",
        runsInPeriod: "周期内运行",
        runsTrend: "近 30 天运行趋势",
        sparklineCaption: "近 30 天趋势",
        topServed: "主要服务对象",
        noServed: "暂无",
        lastRun: "最近运行",
        lastMaintenance: "最近维护",
        pendingFeedback: "待处理反馈",
        noPeople: "该周期内没有人员",
        noMatch: "没有符合条件的 Agent",
        periodActivity: {
          column: "本期",
          ran: "本期运行",
          noRuns: "本期未运行",
        },
        columns: {
          name: "名称",
          jobTitle: "岗位",
          owner: "负责人",
          status: "状态",
          kind: "类型",
          runs: "运行次数",
          failures: "失败",
          latencyTotal: "总耗时",
          latencyP50: "中位耗时",
          cost: "成本",
          served: "服务人数",
          lastRun: "最近运行",
          satisfaction: "满意度",
          lastMaintenance: "最近维护",
          pendingFeedback: "待处理反馈",
          joinedAt: "入职日期",
          actor: "人员",
          ownedAgents: "负责 Agent 数",
          ownedRuns: "负责 Agent 运行量",
          servedByAgents: "被服务 Agent 数",
          calls: "调用次数",
          maintenance: "维护动作数",
        },
      },
      mine: {
        ownedHeading: "我负责的 Agent",
        servedHeading: "为我服务的 Agent",
        noFeedback: "暂无待你处理的反馈",
        noOwned: "你还没有负责的 Agent",
        noServed: "所选周期内没有 Agent 为你服务",
        ratingUp: "有帮助",
        ratingDown: "没帮助",
        ratingNone: "仅评论",
      },
      profile: {
        close: "关闭",
        tabs: {
          basic: "基本信息",
          workload: "工作量",
          history: "履历",
          feedback: "待处理反馈",
          settings: "设置",
        },
        tablistLabel: "Agent 档案分页",
        noOwner: "无负责人",
        servedPeople: "服务对象",
        noServed: "本周期无服务对象",
        currentRevision: "当前修订",
        joinedAt: "入职日期",
        secondaryMetrics: "调用量",
        primaryMetrics: "运行指标",
        generationCalls: "模型调用",
        toolCalls: "工具调用",
        callsPerRun: "平均每次运行调用数",
        abstained: "拒答",
        canceled: "已取消",
        trendRuns: "运行次数",
        trendFailures: "失败",
        runsUnit: "次",
        noHistory: "暂无维护履历。",
        loadMore: "加载更多",
        openRevision: "打开修订",
        maintenanceKinds: {
          revised: "修订",
          skill_upgraded: "Skill 升级",
          disabled: "停用",
          enabled: "启用",
          feedback_resolved: "处理差评",
          owner_transferred: "转交负责人",
        },
        noFeedback: "没有待处理反馈。",
        feedbackCount: "待处理反馈：{count}",
        resolutionNote: "处理说明（可选，最多 500 字）",
        noteTooLong: "说明超过 500 字。",
        jobTitleLabel: "岗位",
        jobTitleTooLong: "岗位超过 64 个字符。",
        save: "保存",
        saved: "已保存",
        currentOwner: "当前负责人",
        newOwnerLabel: "新负责人",
        transferred: "已转交给 {owner}",
        transferFailed: "无法转交负责人。",
        errors: {
          actor_not_found: "在本项目中找不到该人员。",
          agent_read_only: "该 Agent 为只读。",
          job_title_too_long: "岗位超过 64 个字符。",
          note_too_long: "说明超过 500 字。",
          feedback_not_found: "该反馈已不存在。",
          generic: "无法保存更改。",
        },
      },
    },
    migrationBanner: {
      heading: "发现本地草稿",
      descriptionAgent:
        "此设备上保存的 Markdown Agent 草稿，可导入为正式 Agent。",
      descriptionSkill:
        "此设备上保存的 Markdown Skill 草稿，可导入为正式 Skill。",
      import: "导入",
      dismiss: "忽略",
      importing: "正在导入…",
      importFailed: "无法导入该草稿。",
    },
    library: {
      listView: "列表视图",
      cardView: "卡片视图",
      preview: "预览",
      download: "下载文件",
      noPreview: "此来源暂无可用预览。",
      heading: "知识库",
      description: "浏览知识来源，并探索经过编排的领域上下文。",
      addSource: "添加来源",
      all: "文档列表",
      knowledgeGraph: "知识图谱",
      sources: "知识库来源",
      sourceCount: "个来源",
      typeFilter: "类型",
      statusFilter: "状态",
      allTypes: "全部类型",
      allStatuses: "全部状态",
      clearFilters: "清除筛选",
      knowledgeGraphImage: "项目知识图谱",
      graphSummary: "知识图谱摘要",
      concepts: "概念",
      labeledRelationships: "已标注关系",
      search: "搜索知识库",
      sourceFile: "来源文件",
      cancel: "取消",
      ready: "已就绪",
      processing: "处理中",
      failed: "失败",
      noResults: "没有匹配的来源",
      communities: "主题分组",
      selectAllTopics: "全选",
      nodes: "个节点",
      nodeDetails: "节点详情",
      searchResults: "搜索结果",
      noMatchingNodes: "没有匹配的文档、概念或实体，请更换关键词或清除筛选。",
      selectNode: "选择节点以查看其关系。",
      community: "主题分组",
      relationships: "关系",
      provenance: "来源依据",
      connections: "个关联",
      zoomIn: "放大",
      zoomOut: "缩小",
      resetView: "重置视图",
      toggleCommunities: "显示或收起主题分组",
      closeNodeDetails: "关闭节点详情",
      aliases: "别名",
      nodeSources: "来源",
      evidenceSnippets: "证据片段",
      openOriginal: "打开原件",
      askAboutNode: "就此提问",
      showAllRelations: (n: number) => `显示全部 (${n})`,
      relationCount: (n: number) => `${n} 条关系`,
      relationIncoming: "被指向",
      relationOutgoing: "指向",
      highlightedPath: "高亮路径",
      highlightCaption: "回答引用的关系",
      clearHighlight: "返回总览",
      versionUpdated: "图谱版本已更新，部分关系不再可用。",
      highlightLoading: "正在加载高亮路径…",
      highlightError: "高亮路径暂时无法加载。",
      nodeDetailsLoading: "正在加载节点详情…",
      nodeDetailsError: "节点详情暂时无法加载，请重试。",
      enterFullscreen: "进入全屏",
      exitFullscreen: "退出全屏",
      fullscreenUnavailable: "当前浏览器不支持全屏。",
      zoomLevel: "缩放比例",
      extracted: "已抽取",
      inferred: "推断",
      graphNavigationHint: "拖动以平移画布，使用控件缩放，并选择节点查看关系。",
      canvasRegionLabel: "知识图谱画布",
      overviewCaption: "项目知识总览",
      loadMore: "加载更多",
      extractingSources: (n: number) => `${n} 个来源仍在抽取`,
      partialSources: (n: number) => `${n} 个部分失败`,
      sourceCapNotice: (n: number) =>
        `已匹配 ${n} 个来源，图谱仅显示前 50 个。`,
      graphEmpty: "暂无知识图谱。",
      graphEmptyHint: "来源发布后会自动建图。",
      graphFailed: "知识图谱构建失败。",
      graphFailedHint: "请重试构建，或联系运维排查。",
      graphMerging: "图谱正在重建…",
      graphLoading: "正在加载知识图谱…",
      graphUnavailable: "知识图谱暂时无法加载，请重试。",
      otherCommunity: "其他",
      nodeTypes: {
        ENTITY: "实体",
        CONCEPT: "概念",
        REQUIREMENT: "需求",
        SYSTEM: "系统",
        ACTOR: "角色",
        PROCESS: "流程",
      },
      relationTypes: {
        REQUIRES: "需要",
        APPLIES_TO: "适用于",
        PART_OF: "属于",
        EXCEPTION_OF: "例外于",
        SUPERSEDES: "取代",
        TRIGGERS: "触发",
        PRECEDES: "先于",
        VALIDATED_BY: "由其验证",
        RESPONSIBLE_FOR: "负责",
        USES: "使用",
        DEFINES: "定义",
        CONFLICTS_WITH: "与其冲突",
        RELATED_TO: "与其相关",
      },
    },
  },
} as const satisfies Record<Locale, WorkspaceCopy>;
