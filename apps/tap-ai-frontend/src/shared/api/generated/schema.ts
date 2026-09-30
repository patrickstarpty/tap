export interface paths {
    "/api/v1/projects/{project_id}/ai/agents": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Agents */
        get: operations["ai_list_agent_revisions"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/ai/agents/{revision_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Agent */
        get: operations["ai_get_agent_revision"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/ai/models": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Models */
        get: operations["ai_list_models"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/ai/skills": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Skills */
        get: operations["ai_list_skill_revisions"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/ai/skills/{revision_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Skill */
        get: operations["ai_get_skill_revision"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/conversations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Conversations */
        get: operations["conversation_list"];
        put?: never;
        /** Create */
        post: operations["conversation_create"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/conversations/{conversation_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get */
        get: operations["conversation_get"];
        put?: never;
        post?: never;
        /** Delete */
        delete: operations["conversation_delete"];
        options?: never;
        head?: never;
        /** Rename */
        patch: operations["conversation_rename"];
        trace?: never;
    };
    "/api/v1/projects/{project_id}/conversations/{conversation_id}/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Events */
        get: operations["conversation_list_events"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/conversations/{conversation_id}/stream": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Stream */
        get: operations["conversation_stream"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/conversations/{conversation_id}/turns": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Append */
        post: operations["conversation_append_turn"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/conversations/{conversation_id}/turns/{turn_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Cancel */
        post: operations["conversation_cancel_turn"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/conversations/{conversation_id}/turns/{turn_id}/citations/{citation_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Citation */
        get: operations["conversation_get_citation"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/insights/explanations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Explain */
        post: operations["insights_explain_report"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/insights/explanations/{conversation_id}/turns/{turn_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Explanation */
        get: operations["insights_get_explanation"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/answers": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Answer */
        post: operations["knowledge_create_answer"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/chunks/preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Preview Upload */
        post: operations["preview_upload_api_v1_projects__project_id__knowledge_chunks_preview_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/citations/{citation_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Citation */
        get: operations["citation_get_preview"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/documents": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Documents */
        get: operations["knowledge_list_documents"];
        put?: never;
        /**
         * Upload Document
         * @deprecated
         */
        post: operations["knowledge_upload_document"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/documents/{document_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Document */
        get: operations["knowledge_get_document"];
        put?: never;
        post?: never;
        /** Delete Document */
        delete: operations["knowledge_delete_document"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/documents/{document_id}/chunk-settings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Settings */
        get: operations["get_settings_api_v1_projects__project_id__knowledge_documents__document_id__chunk_settings_get"];
        /** Save Settings */
        put: operations["save_settings_api_v1_projects__project_id__knowledge_documents__document_id__chunk_settings_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/documents/{document_id}/chunks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Chunks */
        get: operations["list_chunks_api_v1_projects__project_id__knowledge_documents__document_id__chunks_get"];
        put?: never;
        /** Create Chunk */
        post: operations["create_chunk_api_v1_projects__project_id__knowledge_documents__document_id__chunks_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/documents/{document_id}/chunks/batch": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Batch */
        post: operations["batch_api_v1_projects__project_id__knowledge_documents__document_id__chunks_batch_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/documents/{document_id}/chunks/import": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Import Chunks */
        post: operations["import_chunks_api_v1_projects__project_id__knowledge_documents__document_id__chunks_import_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/documents/{document_id}/chunks/preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Preview */
        post: operations["preview_api_v1_projects__project_id__knowledge_documents__document_id__chunks_preview_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/documents/{document_id}/chunks/{chunk_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Delete Chunk */
        delete: operations["delete_chunk_api_v1_projects__project_id__knowledge_documents__document_id__chunks__chunk_id__delete"];
        options?: never;
        head?: never;
        /** Change Chunk */
        patch: operations["change_chunk_api_v1_projects__project_id__knowledge_documents__document_id__chunks__chunk_id__patch"];
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/documents/{document_id}/chunks/{chunk_id}/children": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Child */
        post: operations["create_child_api_v1_projects__project_id__knowledge_documents__document_id__chunks__chunk_id__children_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/documents/{document_id}/original": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Original */
        get: operations["original_api_v1_projects__project_id__knowledge_documents__document_id__original_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/documents/{document_id}/retry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Retry Document */
        post: operations["knowledge_retry_document"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/documents/{document_id}/review": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Open Document Review */
        post: operations["knowledge_open_document_review"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/graph/evidence/{evidence_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Evidence Detail */
        get: operations["graph_get_evidence"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/graph/nodes/{node_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Node Detail */
        get: operations["graph_get_node"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/graph/nodes/{node_id}/neighbors": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Neighbors */
        post: operations["graph_get_neighbors"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/graph/path": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Bounded Path */
        post: operations["graph_bounded_path"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/graph/query": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Search Graph */
        post: operations["graph_search"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/graph/snapshots": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Active Snapshots */
        get: operations["graph_list_active_snapshots"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/publications/current": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Current Publication */
        get: operations["knowledge_get_current_publication"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/publications/{publication_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Publication */
        get: operations["knowledge_get_publication"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/publications/{publication_id}/withdraw": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Withdraw Publication */
        post: operations["knowledge_withdraw_publication"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/published-sources": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Published Sources */
        get: operations["knowledge_list_published_sources"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/reviews": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Reviews */
        get: operations["knowledge_list_reviews"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/reviews/{review_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Review */
        get: operations["knowledge_get_review"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/reviews/{review_id}/approve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Approve Review */
        post: operations["knowledge_approve_review"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/reviews/{review_id}/decision-history": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Review Decision History */
        get: operations["knowledge_list_review_decision_history"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/reviews/{review_id}/flowchart": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Review Flowchart */
        get: operations["knowledge_get_review_flowchart"];
        /** Correct Review Flowchart */
        put: operations["knowledge_correct_review_flowchart"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/reviews/{review_id}/history": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Review History */
        get: operations["knowledge_list_review_history"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/reviews/{review_id}/inventory": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Review Inventory */
        get: operations["knowledge_list_review_inventory"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/reviews/{review_id}/items/{item_id}/comparison": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Compare Review Item */
        get: operations["knowledge_compare_review_item"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/reviews/{review_id}/items/{item_id}/decision": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Update Review Item Decision */
        put: operations["knowledge_update_review_item_decision"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/reviews/{review_id}/items/{item_id}/original": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read Review Original */
        get: operations["knowledge_read_review_original"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/reviews/{review_id}/items/{item_id}/original-image": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read Review Original Image */
        get: operations["knowledge_read_review_original_image"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/reviews/{review_id}/publications": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Review Publications */
        get: operations["knowledge_list_review_publications"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/reviews/{review_id}/publish": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Publish Review */
        post: operations["knowledge_publish_review"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/reviews/{review_id}/return": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Return Review */
        post: operations["knowledge_return_review"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/reviews/{review_id}/submit": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Submit Review */
        post: operations["knowledge_submit_review"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/sources": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Sources */
        get: operations["knowledge_list_sources"];
        put?: never;
        /** Upload Source */
        post: operations["knowledge_upload_source"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/sources/{source_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Source */
        get: operations["knowledge_get_source"];
        put?: never;
        post?: never;
        /** Delete Source */
        delete: operations["knowledge_delete_source"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/knowledge/sources/{source_id}/retry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Retry Source */
        post: operations["knowledge_retry_source"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/prompt-suggestions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Prompt Suggestions */
        get: operations["prompt_suggestion_list"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/test-plans": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Revisions */
        get: operations["test_plan_list_revisions"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/test-plans/generations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Request Generation */
        post: operations["test_plan_request_generation"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/test-plans/generations/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Generation */
        get: operations["test_plan_get_generation"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/test-plans/generations/{job_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Cancel Generation */
        post: operations["test_plan_cancel_generation"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/test-plans/generations/{job_id}/retry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Retry Generation */
        post: operations["test_plan_retry_generation"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/test-plans/reviews/summary": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Review Summary */
        get: operations["test_plan_review_summary"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/test-plans/{test_plan_id}/revisions/{revision_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Revision */
        get: operations["test_plan_get_revision"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Replace Draft */
        patch: operations["test_plan_replace_draft"];
        trace?: never;
    };
    "/api/v1/projects/{project_id}/test-plans/{test_plan_id}/revisions/{revision_id}/evidence/{citation_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Evidence Preview */
        get: operations["test_plan_get_evidence_preview"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/test-plans/{test_plan_id}/revisions/{revision_id}/fork": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Fork Revision */
        post: operations["test_plan_fork_revision"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/test-plans/{test_plan_id}/revisions/{revision_id}/publish": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Publish Revision */
        post: operations["test_plan_publish_revision"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/test-plans/{test_plan_id}/revisions/{revision_id}/reviews": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Review Revision */
        post: operations["test_plan_review_revision"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/runtime-mode": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Runtime Mode */
        get: operations["runtime_get_mode"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/health/live": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Live Health */
        get: operations["health_get_live"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/health/ready": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Ready Health */
        get: operations["health_get_ready"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/chats/{chat_id}/turns": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Create Chat Turn
         * @description Reserve the public route until the durable turn workflow is implemented.
         */
        post: operations["chat_create_turn"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /**
         * AbstentionReason
         * @enum {string}
         */
        AbstentionReason: "insufficient_evidence" | "conflicting_sources" | "revision_mismatch";
        /** AiAgentRevisionPage */
        AiAgentRevisionPage: {
            /** Items */
            items: components["schemas"]["AiAgentRevisionSummary"][];
        };
        /** AiAgentRevisionSummary */
        AiAgentRevisionSummary: {
            /** Assetid */
            assetId: string;
            /** Contentdigest */
            contentDigest: string;
            /** Displayname */
            displayName: string;
            /** Outputschemadigest */
            outputSchemaDigest: string;
            /** Revisionid */
            revisionId: string;
            /** Toolallowlist */
            toolAllowlist: ("knowledge.search" | "knowledge.answer")[];
        };
        /** AnswerClaim */
        AnswerClaim: {
            /** Answerend */
            answerEnd: number;
            /** Answerstart */
            answerStart: number;
            /** Citationids */
            citationIds: string[];
            /** Claimid */
            claimId: string;
            /** Text */
            text: string;
        };
        /** AnswerDeltaEvent */
        AnswerDeltaEvent: {
            payload: components["schemas"]["AnswerDeltaPayload"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "answer.delta";
        };
        /** AnswerDeltaPayload */
        AnswerDeltaPayload: {
            /** Text */
            text: string;
        };
        /**
         * AnswerMode
         * @enum {string}
         */
        AnswerMode: "quick" | "deep";
        /** BddAnchor */
        BddAnchor: {
            /** Featureid */
            featureId: string;
            /**
             * Scenarioid
             * @default null
             */
            scenarioId?: string | null;
            /**
             * Stepid
             * @default null
             */
            stepId?: string | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "bdd";
        };
        /** BddAnchor */
        "BddAnchor-Input": {
            /** Featureid */
            featureId: string;
            /** Scenarioid */
            scenarioId?: string | null;
            /** Stepid */
            stepId?: string | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "bdd";
        };
        /** Body_knowledge_upload_document */
        Body_knowledge_upload_document: {
            /** Settings */
            settings?: string | null;
            /**
             * Upload
             * Format: binary
             */
            upload: string;
        };
        /** Body_knowledge_upload_source */
        Body_knowledge_upload_source: {
            /** Settings */
            settings?: string | null;
            /**
             * Upload
             * Format: binary
             */
            upload: string;
        };
        /** Body_preview_upload_api_v1_projects__project_id__knowledge_chunks_preview_post */
        Body_preview_upload_api_v1_projects__project_id__knowledge_chunks_preview_post: {
            /** Settings */
            settings: string;
            /**
             * Upload
             * Format: binary
             */
            upload: string;
        };
        /**
         * ChatEventEnvelope
         * @description A recoverable, ordered event persisted for one chat turn.
         */
        ChatEventEnvelope: {
            /** Chatid */
            chatId: string;
            /** Event */
            event: components["schemas"]["TurnStartedEvent"] | components["schemas"]["ContextAssembledEvent"] | components["schemas"]["QueryPlanReadyEvent"] | components["schemas"]["StageStartedEvent"] | components["schemas"]["StageCompletedEvent"] | components["schemas"]["RetrievalHitsReadyEvent"] | components["schemas"]["RerankCompletedEvent"] | components["schemas"]["AnswerDeltaEvent"] | components["schemas"]["CitationResolvedEvent"] | components["schemas"]["TurnCompletedEvent"] | components["schemas"]["TurnAbstainedEvent"] | components["schemas"]["TurnDegradedEvent"] | components["schemas"]["TurnCanceledEvent"] | components["schemas"]["TurnFailedEvent"] | components["schemas"]["ConversationTurnRequestedEvent"] | components["schemas"]["ConversationTurnCompletedEvent"] | components["schemas"]["TestPlanGenerationWaitingEvent"] | components["schemas"]["TestPlanGenerationResultEvent"] | components["schemas"]["TestPlanGenerationFailedEvent"] | components["schemas"]["TestPlanGenerationCanceledEvent"];
            /** Eventid */
            eventId: string;
            /** Occurredat */
            occurredAt: string;
            /** Schemaversion */
            schemaVersion: number;
            /** Sequence */
            sequence: number;
            /** Turnid */
            turnId: string;
        };
        /**
         * ChatTurnAccepted
         * @description The durable identity returned after a turn has been accepted for processing.
         */
        ChatTurnAccepted: {
            /** Chatid */
            chatId: string;
            /**
             * State
             * @constant
             */
            state: "queued";
            /** Turnid */
            turnId: string;
        };
        /**
         * ChatTurnRequest
         * @description A browser request to create one turn in an existing chat.
         */
        ChatTurnRequest: {
            /** @default quick */
            answerMode?: components["schemas"]["AnswerMode"];
            /** Clientrequestid */
            clientRequestId: string;
            /** Message */
            message: string;
            /** Requestedcorpusversion */
            requestedCorpusVersion?: string | null;
            /** Requestedenvironment */
            requestedEnvironment?: string | null;
            /** Resourcerefs */
            resourceRefs?: components["schemas"]["ResourceRef"][] | null;
            /** Sourcescope */
            sourceScope?: components["schemas"]["SourceFamily"][] | null;
        };
        /** Citation */
        Citation: {
            /** Approvaldigest */
            approvalDigest?: string | null;
            /** Approveditemid */
            approvedItemId?: string | null;
            /** Chunkcontenthash */
            chunkContentHash: string;
            /** Chunkid */
            chunkId: string;
            /** Citationid */
            citationId: string;
            contentRole: components["schemas"]["ContentRole"];
            /** Derivedfromchunkids */
            derivedFromChunkIds?: string[] | null;
            /** Evidencelabel */
            evidenceLabel: string;
            /** Logicalchunkid */
            logicalChunkId: string;
            /** Publicationid */
            publicationId?: string | null;
            source: components["schemas"]["SourceRevisionRef"];
        };
        /** CitationPreview */
        CitationPreview: {
            anchor: components["schemas"]["StructuralAnchor"];
            /** Chunkcontenthash */
            chunkContentHash: string;
            /** Citationid */
            citationId: string;
            /** Documentid */
            documentId: string;
            /** Filename */
            filename: string;
            /**
             * Prefix
             * @default
             */
            prefix?: string;
            /** Quote */
            quote: string;
            /** Revisionid */
            revisionId: string;
            /** Sourcecontenthash */
            sourceContentHash: string;
            /**
             * Suffix
             * @default
             */
            suffix?: string;
        };
        /** CitationResolvedEvent */
        CitationResolvedEvent: {
            payload: components["schemas"]["CitationResolvedPayload"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "citation.resolved";
        };
        /** CitationResolvedPayload */
        CitationResolvedPayload: {
            citation: components["schemas"]["Citation"];
        };
        /** CodeAnchor */
        CodeAnchor: {
            /** Lineend */
            lineEnd: number;
            /** Linestart */
            lineStart: number;
            /** Path */
            path: string;
            /** Repo */
            repo: string;
            /**
             * Symbol
             * @default null
             */
            symbol?: string | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "code";
        };
        /** CodeAnchor */
        "CodeAnchor-Input": {
            /** Lineend */
            lineEnd: number;
            /** Linestart */
            lineStart: number;
            /** Path */
            path: string;
            /** Repo */
            repo: string;
            /** Symbol */
            symbol?: string | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "code";
        };
        /**
         * ContentRole
         * @enum {string}
         */
        ContentRole: "source" | "generated_summary";
        /** ContextAssembledEvent */
        ContextAssembledEvent: {
            payload: components["schemas"]["ContextAssembledPayload"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "context.assembled";
        };
        /** ContextAssembledPayload */
        ContextAssembledPayload: {
            /** Contextsnapshotid */
            contextSnapshotId: string;
            /** Tokencount */
            tokenCount: number;
        };
        /** ConversationAccepted */
        ConversationAccepted: {
            /** Conversationid */
            conversationId: string;
            /**
             * State
             * @constant
             */
            state: "queued";
            /** Turnid */
            turnId: string;
        };
        /** ConversationCreateRequest */
        ConversationCreateRequest: {
            /** Agentrevisionid */
            agentRevisionId?: string | null;
            /**
             * Documentrevisionids
             * @default []
             */
            documentRevisionIds?: string[];
            /** Message */
            message: string;
            /** Modelalias */
            modelAlias: string;
            /**
             * Skillrevisionids
             * @default []
             */
            skillRevisionIds?: string[];
            /**
             * Sourcerevisionids
             * @default []
             */
            sourceRevisionIds?: string[];
        };
        /** ConversationDetail */
        ConversationDetail: {
            /** Conversationid */
            conversationId: string;
            /** Createdat */
            createdAt: string;
            /** Title */
            title: string;
            /** Turns */
            turns: components["schemas"]["ConversationTurnSummary"][];
            /** Updatedat */
            updatedAt: string;
        };
        /** ConversationEventItem */
        ConversationEventItem: {
            /** Eventid */
            eventId: string;
            /**
             * Eventtype
             * @enum {string}
             */
            eventType: "turn.started" | "context.assembled" | "query.plan_ready" | "stage.started" | "stage.completed" | "retrieval.hits_ready" | "rerank.completed" | "answer.delta" | "citation.resolved" | "turn.completed" | "turn.abstained" | "turn.degraded" | "turn.canceled" | "turn.failed" | "conversation.turn.requested" | "conversation.turn.completed" | "test-plan.generation.waiting" | "test-plan.generation.result_ready" | "test-plan.generation.failed" | "test-plan.generation.canceled";
            /** Occurredat */
            occurredAt: string;
            /** Payload */
            payload: {
                [key: string]: unknown;
            };
            /** Sequence */
            sequence: number;
            /** Turnid */
            turnId: string;
        };
        /** ConversationEventPage */
        ConversationEventPage: {
            /** Items */
            items: components["schemas"]["ConversationEventItem"][];
        };
        /** ConversationPage */
        ConversationPage: {
            /** Items */
            items: components["schemas"]["ConversationSummary"][];
            /** Nextcursor */
            nextCursor?: string | null;
        };
        /**
         * ConversationRenameRequest
         * @description Owner-only title change; surrounding whitespace is removed before bounds apply.
         */
        ConversationRenameRequest: {
            /** Title */
            title: string;
        };
        /** ConversationResolvedResourceView */
        ConversationResolvedResourceView: {
            /** Documentid */
            documentId: string;
            /** Documentrevisionid */
            documentRevisionId: string;
            /** Label */
            label: string;
            /** Sourceid */
            sourceId: string;
            /** Sourcerevisionid */
            sourceRevisionId?: string | null;
        };
        /** ConversationSummary */
        ConversationSummary: {
            /** Conversationid */
            conversationId: string;
            /** Createdat */
            createdAt: string;
            /** Title */
            title: string;
            /** Updatedat */
            updatedAt: string;
        };
        /** ConversationTurnCompletedEvent */
        ConversationTurnCompletedEvent: {
            payload: components["schemas"]["ConversationTurnCompletedPayload"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "conversation.turn.completed";
        };
        /** ConversationTurnCompletedPayload */
        ConversationTurnCompletedPayload: {
            /** Answerevidencesnapshotdigest */
            answerEvidenceSnapshotDigest: string;
            /** Answerevidencesnapshotid */
            answerEvidenceSnapshotId: string;
            /**
             * Outcome
             * @enum {string}
             */
            outcome: "completed" | "abstained" | "canceled" | "failed";
            /** Turnid */
            turnId: string;
        };
        /**
         * ConversationTurnInputView
         * @description Browser-safe immutable input facts; excludes instructions and policy internals.
         */
        ConversationTurnInputView: {
            /** Agentlabel */
            agentLabel?: string | null;
            /** Agentrevisionid */
            agentRevisionId?: string | null;
            /** Documentrevisionids */
            documentRevisionIds: string[];
            /** Insightsqueryid */
            insightsQueryId?: string | null;
            /** Message */
            message: string;
            /** Modelalias */
            modelAlias: string;
            /** Resolvedresources */
            resolvedResources: components["schemas"]["ConversationResolvedResourceView"][];
            /** Skilllabels */
            skillLabels: string[];
            /** Skillrevisionids */
            skillRevisionIds: string[];
            /** Sourcerevisionids */
            sourceRevisionIds: string[];
        };
        /** ConversationTurnRequestedEvent */
        ConversationTurnRequestedEvent: {
            payload: components["schemas"]["ConversationTurnRequestedPayload"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "conversation.turn.requested";
        };
        /** ConversationTurnRequestedPayload */
        ConversationTurnRequestedPayload: {
            /** Conversationid */
            conversationId: string;
            /** Inputsnapshotdigest */
            inputSnapshotDigest: string;
            /** Turnid */
            turnId: string;
        };
        /** ConversationTurnSummary */
        ConversationTurnSummary: {
            /** Answerevidencesnapshotdigest */
            answerEvidenceSnapshotDigest?: string | null;
            /** Answerevidencesnapshotid */
            answerEvidenceSnapshotId?: string | null;
            /** Attempt */
            attempt: number;
            /** Graphcontextstatus */
            graphContextStatus?: ("APPLIED" | "NOT_READY" | "FAILED" | "UNAVAILABLE" | "NOT_SELECTED") | null;
            /** Graphsnapshotid */
            graphSnapshotId?: string | null;
            input: components["schemas"]["ConversationTurnInputView"];
            /** Inputsnapshotdigest */
            inputSnapshotDigest: string;
            /**
             * State
             * @enum {string}
             */
            state: "queued" | "running" | "completed" | "abstained" | "canceled" | "failed";
            /** Turnid */
            turnId: string;
        };
        /** DocumentAccepted */
        DocumentAccepted: {
            document: components["schemas"]["DocumentSummary"];
            /** Duplicate */
            duplicate: boolean;
            /** Jobid */
            jobId: string;
        };
        /** DocumentAnchor */
        DocumentAnchor: {
            /**
             * Bbox
             * @default null
             */
            bbox?: number[] | null;
            /**
             * Endoffset
             * @default null
             */
            endOffset?: number | null;
            /**
             * Headingpath
             * @default null
             */
            headingPath?: string[] | null;
            /**
             * Inventoryitemid
             * @default null
             */
            inventoryItemId?: string | null;
            /**
             * Page
             * @default null
             */
            page?: number | null;
            /**
             * Startoffset
             * @default null
             */
            startOffset?: number | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "document";
        };
        /** DocumentAnchor */
        "DocumentAnchor-Input": {
            /** Bbox */
            bbox?: number[] | null;
            /** Endoffset */
            endOffset?: number | null;
            /** Headingpath */
            headingPath?: string[] | null;
            /** Inventoryitemid */
            inventoryItemId?: string | null;
            /** Page */
            page?: number | null;
            /** Startoffset */
            startOffset?: number | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "document";
        };
        /** DocumentDetail */
        DocumentDetail: {
            /** Chunkcount */
            chunkCount: number;
            /** Documentid */
            documentId: string;
            /**
             * Errorcode
             * @default null
             */
            errorCode?: string | null;
            /**
             * Errorsummary
             * @default null
             */
            errorSummary?: string | null;
            /** Filename */
            filename: string;
            /**
             * Mediatype
             * @enum {string}
             */
            mediaType: "application/pdf" | "application/vnd.openxmlformats-officedocument.wordprocessingml.document" | "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" | "text/markdown" | "text/plain" | "image/png" | "image/jpeg";
            /**
             * Normalizedpreview
             * @default null
             */
            normalizedPreview?: string | null;
            /** Revisionid */
            revisionId: string;
            /** Sourcecontenthash */
            sourceContentHash: string;
            /** Sourceid */
            sourceId: string;
            stage: components["schemas"]["IngestionStage"];
            /** Stages */
            stages: components["schemas"]["DocumentStageSnapshot"][];
            status: components["schemas"]["DocumentStatus"];
            /** Updatedat */
            updatedAt: string;
        };
        /** DocumentPage */
        DocumentPage: {
            /** Items */
            items: components["schemas"]["DocumentSummary"][];
            /**
             * Nextcursor
             * @default null
             */
            nextCursor?: string | null;
        };
        /** DocumentStageSnapshot */
        DocumentStageSnapshot: {
            /**
             * Completedat
             * @default null
             */
            completedAt?: string | null;
            /**
             * Errorcode
             * @default null
             */
            errorCode?: string | null;
            stage: components["schemas"]["IngestionStage"];
            state: components["schemas"]["DocumentStageState"];
        };
        /**
         * DocumentStageState
         * @enum {string}
         */
        DocumentStageState: "pending" | "processing" | "completed" | "failed";
        /**
         * DocumentStatus
         * @enum {string}
         */
        DocumentStatus: "queued" | "processing" | "ready" | "failed" | "deleting";
        /** DocumentSummary */
        DocumentSummary: {
            /** Chunkcount */
            chunkCount: number;
            /** Documentid */
            documentId: string;
            /**
             * Errorcode
             * @default null
             */
            errorCode?: string | null;
            /**
             * Errorsummary
             * @default null
             */
            errorSummary?: string | null;
            /** Filename */
            filename: string;
            /**
             * Mediatype
             * @enum {string}
             */
            mediaType: "application/pdf" | "application/vnd.openxmlformats-officedocument.wordprocessingml.document" | "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" | "text/markdown" | "text/plain" | "image/png" | "image/jpeg";
            /** Sourceid */
            sourceId: string;
            stage: components["schemas"]["IngestionStage"];
            status: components["schemas"]["DocumentStatus"];
            /** Updatedat */
            updatedAt: string;
        };
        /** FailureAnchor */
        FailureAnchor: {
            /** Incidentid */
            incidentId: string;
            /**
             * Runid
             * @default null
             */
            runId?: string | null;
            /**
             * Timeend
             * @default null
             */
            timeEnd?: string | null;
            /**
             * Timestart
             * @default null
             */
            timeStart?: string | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "failure";
        };
        /** FailureAnchor */
        "FailureAnchor-Input": {
            /** Incidentid */
            incidentId: string;
            /** Runid */
            runId?: string | null;
            /** Timeend */
            timeEnd?: string | null;
            /** Timestart */
            timeStart?: string | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "failure";
        };
        /** GraphEdgeView */
        GraphEdgeView: {
            /** Confidence */
            confidence: number;
            /** Edgeid */
            edgeId: string;
            /**
             * Evidenceids
             * @default []
             */
            evidenceIds?: string[];
            /**
             * Origin
             * @enum {string}
             */
            origin: "EXTRACTED" | "INFERRED";
            /** Relationtype */
            relationType: string;
            /** Sourcenodeid */
            sourceNodeId: string;
            /** Targetnodeid */
            targetNodeId: string;
        };
        /** GraphEvidenceView */
        GraphEvidenceView: {
            /** Anchor */
            anchor: {
                [key: string]: unknown;
            };
            /** Chunkid */
            chunkId: string;
            /** Contentdigest */
            contentDigest: string;
            /** Documentrevisionid */
            documentRevisionId: string;
            /** Evidenceid */
            evidenceId: string;
            /** Sourcerevisionid */
            sourceRevisionId: string;
        };
        /** GraphNeighborRequest */
        GraphNeighborRequest: {
            /**
             * Depth
             * @default 1
             */
            depth?: number;
            /**
             * Nodelimit
             * @default 50
             */
            nodeLimit?: number;
            /** Snapshotid */
            snapshotId: string;
        };
        /** GraphNodeView */
        GraphNodeView: {
            /** Canonicalkey */
            canonicalKey: string;
            /**
             * Evidenceids
             * @default []
             */
            evidenceIds?: string[];
            /** Label */
            label: string;
            /** Nodeid */
            nodeId: string;
            /** Nodetype */
            nodeType: string;
        };
        /** GraphPathRequest */
        GraphPathRequest: {
            /**
             * Nodelimit
             * @default 50
             */
            nodeLimit?: number;
            /** Snapshotid */
            snapshotId: string;
            /** Sourcenodeid */
            sourceNodeId: string;
            /** Targetnodeid */
            targetNodeId: string;
        };
        /** GraphSearchRequest */
        GraphSearchRequest: {
            /**
             * Nodelimit
             * @default 50
             */
            nodeLimit?: number;
            /** Query */
            query: string;
            /** Snapshotid */
            snapshotId: string;
        };
        /** GraphSnapshotPage */
        GraphSnapshotPage: {
            /** Items */
            items: components["schemas"]["GraphSnapshotView"][];
        };
        /** GraphSnapshotView */
        GraphSnapshotView: {
            /** Documentrevisionids */
            documentRevisionIds: string[];
            /** Snapshotid */
            snapshotId: string;
            /** Sourcerevisionids */
            sourceRevisionIds: string[];
            /** Sourcesetdigest */
            sourceSetDigest: string;
            /**
             * Status
             * @enum {string}
             */
            status: "CANDIDATE" | "READY" | "FAILED";
        };
        /** GraphSubgraphView */
        GraphSubgraphView: {
            /** Edges */
            edges: components["schemas"]["GraphEdgeView"][];
            /**
             * Evidence
             * @default []
             */
            evidence?: components["schemas"]["GraphEvidenceView"][];
            /** Nodes */
            nodes: components["schemas"]["GraphNodeView"][];
            /** Snapshotid */
            snapshotId: string;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** HealthComponent */
        HealthComponent: {
            /**
             * Detail
             * @default null
             */
            detail?: string | null;
            name: components["schemas"]["HealthComponentName"];
            /** @default null */
            remediationCode?: components["schemas"]["HealthRemediationCode"] | null;
            state: components["schemas"]["HealthComponentState"];
        };
        /**
         * HealthComponentName
         * @enum {string}
         */
        HealthComponentName: "mysql" | "redis" | "blob" | "milvus" | "models";
        /**
         * HealthComponentState
         * @enum {string}
         */
        HealthComponentState: "ok" | "failed";
        /**
         * HealthRemediationCode
         * @enum {string}
         */
        HealthRemediationCode: "start-mysql" | "start-redis" | "start-blob" | "start-milvus" | "configure-models";
        /**
         * IngestionStage
         * @enum {string}
         */
        IngestionStage: "stored" | "parsing" | "chunking" | "embedding" | "publishing" | "ready";
        /** InsightsEvidenceExcerpt */
        InsightsEvidenceExcerpt: {
            /** Approvaldigest */
            approvalDigest?: string | null;
            /** Approveditemid */
            approvedItemId?: string | null;
            /** Chunkcontenthash */
            chunkContentHash?: string | null;
            /** Chunkid */
            chunkId?: string | null;
            /** Citationid */
            citationId: string;
            /** Evidenceversion */
            evidenceVersion?: string | null;
            /** Page */
            page?: number | null;
            /** Publicationid */
            publicationId?: string | null;
            /** Revisionid */
            revisionId?: string | null;
            /** Sourcecontenthash */
            sourceContentHash?: string | null;
            /** Sourceid */
            sourceId?: string | null;
            /** Text */
            text: string;
        };
        /** InsightsExplanationAccepted */
        InsightsExplanationAccepted: {
            /** Conversationid */
            conversationId: string;
            /**
             * State
             * @enum {string}
             */
            state: "queued" | "running" | "completed" | "abstained" | "canceled" | "failed";
            /** Turnid */
            turnId: string;
        };
        /** InsightsExplanationFact */
        InsightsExplanationFact: {
            /**
             * Completeness
             * @enum {string}
             */
            completeness: "complete" | "empty" | "unavailable";
            /** Denominator */
            denominator: number | null;
            /** Evidencerefs */
            evidenceRefs: string[];
            /** Metricid */
            metricId: string;
            /** Missingreasons */
            missingReasons: string[];
            /** Numerator */
            numerator: number | null;
            /** Value */
            value: number | null;
        };
        /** InsightsExplanationRequest */
        InsightsExplanationRequest: {
            /** Conversationid */
            conversationId?: string | null;
            /**
             * Documentrevisionids
             * @default []
             */
            documentRevisionIds?: string[];
            /** Queryid */
            queryId: string;
            /** Question */
            question: string;
            /** Resourcerefs */
            resourceRefs: string[];
            /**
             * Sourcerevisionids
             * @default []
             */
            sourceRevisionIds?: string[];
        };
        /** InsightsExplanationResult */
        InsightsExplanationResult: {
            /** Asof */
            asOf: string | null;
            /** Evidenceexcerpts */
            evidenceExcerpts: components["schemas"]["InsightsEvidenceExcerpt"][];
            /** Factwatermark */
            factWatermark?: {
                [key: string]: unknown;
            } | null;
            /** Facts */
            facts: components["schemas"]["InsightsExplanationFact"][];
            /** Hypotheses */
            hypotheses: string[];
            /**
             * Knowledgesearchperformed
             * @default false
             */
            knowledgeSearchPerformed?: boolean;
            /** Metricversion */
            metricVersion: string | null;
            /** Missinginformation */
            missingInformation: string[];
            /** Queryid */
            queryId: string | null;
            /** Reportcoverage */
            reportCoverage: {
                [key: string]: unknown;
            }[];
            /**
             * Stopreason
             * @enum {string}
             */
            stopReason: "completed" | "budget-exhausted" | "insights-unavailable";
        };
        /** KnowledgeChunk */
        KnowledgeChunk: {
            /** Charcount */
            charCount: number;
            /** Children */
            children?: components["schemas"]["KnowledgeChunk"][];
            /** Chunkid */
            chunkId: string;
            /** Content */
            content: string;
            /** Edited */
            edited: boolean;
            /** Enabled */
            enabled: boolean;
            /** Indexerror */
            indexError?: string | null;
            /**
             * Indexstatus
             * @enum {string}
             */
            indexStatus: "pending" | "ready" | "error";
            /** Keywords */
            keywords?: string[];
            /** Position */
            position: number;
            /** Summary */
            summary?: string | null;
            /** Tokens */
            tokens: number;
            /** Version */
            version: number;
        };
        /** KnowledgeChunkBatch */
        KnowledgeChunkBatch: {
            /**
             * Action
             * @enum {string}
             */
            action: "enable" | "disable" | "delete" | "retry";
            /** Items */
            items: components["schemas"]["KnowledgeChunkVersion"][];
        };
        /** KnowledgeChunkBatchResult */
        KnowledgeChunkBatchResult: {
            /** Failed */
            failed: components["schemas"]["KnowledgeChunkFailure"][];
            /** Succeeded */
            succeeded: string[];
        };
        /** KnowledgeChunkChange */
        KnowledgeChunkChange: {
            /** Content */
            content?: string | null;
            /** Enabled */
            enabled?: boolean | null;
            /**
             * Regeneratechildren
             * @default false
             */
            regenerateChildren?: boolean;
            /** Version */
            version: number;
        };
        /** KnowledgeChunkCreate */
        KnowledgeChunkCreate: {
            /** Content */
            content: string;
        };
        /** KnowledgeChunkFailure */
        KnowledgeChunkFailure: {
            /** Chunkid */
            chunkId: string;
            /** Error */
            error: string;
        };
        /** KnowledgeChunkImport */
        KnowledgeChunkImport: {
            /** Contents */
            contents: string[];
        };
        /** KnowledgeChunkPage */
        KnowledgeChunkPage: {
            /** Items */
            items: components["schemas"]["KnowledgeChunk"][];
            /** Page */
            page: number;
            /** Pagesize */
            pageSize: number;
            /** Total */
            total: number;
        };
        /** KnowledgeChunkPreview */
        KnowledgeChunkPreview: {
            /** Items */
            items: components["schemas"]["KnowledgeChunk"][];
            /** Total */
            total: number;
        };
        /** KnowledgeChunkSettings */
        KnowledgeChunkSettings: {
            /**
             * Childmaxlength
             * @default 256
             */
            childMaxLength?: number;
            /**
             * Childseparator
             * @default
             */
            childSeparator?: string;
            /**
             * Maxlength
             * @default 1024
             */
            maxLength?: number;
            /**
             * Mode
             * @default general
             * @enum {string}
             */
            mode?: "general" | "parent_child";
            /**
             * Overlap
             * @default 50
             */
            overlap?: number;
            /**
             * Parentmode
             * @default paragraph
             * @enum {string}
             */
            parentMode?: "paragraph" | "full_doc";
            /**
             * Removeurls
             * @default false
             */
            removeUrls?: boolean;
            /**
             * Replacewhitespace
             * @default false
             */
            replaceWhitespace?: boolean;
            /**
             * Separator
             * @default
             */
            separator?: string;
        };
        /** KnowledgeChunkSettingsRequest */
        KnowledgeChunkSettingsRequest: {
            settings: components["schemas"]["KnowledgeChunkSettings"];
        };
        /** KnowledgeChunkSettingsSave */
        KnowledgeChunkSettingsSave: {
            /**
             * Confirmreplace
             * @default false
             */
            confirmReplace?: boolean;
            settings: components["schemas"]["KnowledgeChunkSettings"];
            /** Version */
            version: number;
        };
        /** KnowledgeChunkSettingsView */
        KnowledgeChunkSettingsView: {
            /** Originalrevisionid */
            originalRevisionId: string;
            settings: components["schemas"]["KnowledgeChunkSettings"];
            /** Version */
            version: number;
        };
        /** KnowledgeChunkVersion */
        KnowledgeChunkVersion: {
            /** Chunkid */
            chunkId: string;
            /** Version */
            version: number;
        };
        /** KnowledgeFlowchart */
        KnowledgeFlowchart: {
            /** Edges */
            edges: components["schemas"]["KnowledgeFlowchartEdge"][];
            /** Nodes */
            nodes: components["schemas"]["KnowledgeFlowchartNode"][];
        };
        /** KnowledgeFlowchartCorrection */
        KnowledgeFlowchartCorrection: {
            /** Sourcerevisionid */
            sourceRevisionId: string;
        };
        /** KnowledgeFlowchartEdge */
        KnowledgeFlowchartEdge: {
            /** Certain */
            certain: boolean;
            /** Condition */
            condition: string;
            /** Source */
            source: string;
            /** Target */
            target: string;
        };
        /** KnowledgeFlowchartNode */
        KnowledgeFlowchartNode: {
            /** Box */
            box: number[];
            /** Id */
            id: string;
            /** Label */
            label: string;
            /** Lane */
            lane: string;
        };
        /** KnowledgePublicationDetail */
        KnowledgePublicationDetail: {
            /** Approvaldigest */
            approvalDigest: string;
            /** Approveditemids */
            approvedItemIds: string[];
            /** Expiresat */
            expiresAt: string;
            /** Generation */
            generation: string;
            /** Publicationid */
            publicationId: string;
            /** Publishedat */
            publishedAt: string;
            /** Reviewid */
            reviewId: string;
            /** Reviewversion */
            reviewVersion: number;
            /** Sourcerevisionids */
            sourceRevisionIds: string[];
            /**
             * Status
             * @enum {string}
             */
            status: "published" | "withdrawn";
            /** Version */
            version: number;
            /** Withdrawnat */
            withdrawnAt?: string | null;
            /** Withdrawnby */
            withdrawnBy?: string | null;
        };
        /** KnowledgePublicationPage */
        KnowledgePublicationPage: {
            /** Items */
            items: components["schemas"]["KnowledgePublicationDetail"][];
            /** Nextcursor */
            nextCursor?: string | null;
            /** Totalcount */
            totalCount: number;
        };
        /** KnowledgePublicationTarget */
        KnowledgePublicationTarget: {
            /** Generation */
            generation?: string | null;
            /** Reason */
            reason?: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ready" | "unavailable";
        };
        /** KnowledgePublishRequest */
        KnowledgePublishRequest: {
            /** Generation */
            generation: string;
        };
        /**
         * KnowledgeReviewAction
         * @enum {string}
         */
        KnowledgeReviewAction: "edit" | "submit" | "return" | "approve" | "publish" | "withdraw" | "read_original";
        /**
         * KnowledgeReviewCheckKind
         * @enum {string}
         */
        KnowledgeReviewCheckKind: "scope" | "term" | "amount" | "unit" | "exception";
        /** KnowledgeReviewDecisionPage */
        KnowledgeReviewDecisionPage: {
            /** Items */
            items: components["schemas"]["KnowledgeReviewItemDecisionDetail"][];
            /** Nextcursor */
            nextCursor?: number | null;
            /** Totalcount */
            totalCount: number;
        };
        /** KnowledgeReviewDecisionRequest */
        KnowledgeReviewDecisionRequest: {
            checkKind: components["schemas"]["KnowledgeReviewCheckKind"];
            /** Note */
            note: string;
            status: components["schemas"]["KnowledgeReviewDecisionStatus"];
        };
        /**
         * KnowledgeReviewDecisionStatus
         * @enum {string}
         */
        KnowledgeReviewDecisionStatus: "accepted" | "blocked" | "excluded";
        /** KnowledgeReviewDetail */
        KnowledgeReviewDetail: {
            /** Allowedactions */
            allowedActions: components["schemas"]["KnowledgeReviewAction"][];
            /** Approvaldigest */
            approvalDigest: string;
            /** Approveditemids */
            approvedItemIds: string[];
            /** Blockingitemids */
            blockingItemIds: string[];
            currentPublication?: components["schemas"]["KnowledgePublicationDetail"] | null;
            /** Decisionhistory */
            decisionHistory: components["schemas"]["KnowledgeReviewItemDecisionDetail"][];
            /** Decisionhistorynextcursor */
            decisionHistoryNextCursor?: number | null;
            /** Decisionhistorytotalcount */
            decisionHistoryTotalCount: number;
            /** Decisions */
            decisions: components["schemas"]["KnowledgeReviewItemDecisionDetail"][];
            /** Editoractorids */
            editorActorIds: string[];
            /** Expiresat */
            expiresAt: string;
            /** History */
            history: components["schemas"]["KnowledgeReviewHistoryDetail"][];
            /** Historynextcursor */
            historyNextCursor?: number | null;
            /** Historytotalcount */
            historyTotalCount: number;
            inventory: components["schemas"]["KnowledgeReviewInventory"];
            /** Publicationids */
            publicationIds: string[];
            /** Publicationnextcursor */
            publicationNextCursor?: string | null;
            publicationTarget: components["schemas"]["KnowledgePublicationTarget"];
            /** Publicationtotalcount */
            publicationTotalCount: number;
            /** Reviewid */
            reviewId: string;
            /** Revieweractorid */
            reviewerActorId?: string | null;
            /** Sourcerevisionids */
            sourceRevisionIds: string[];
            status: components["schemas"]["KnowledgeReviewStatus"];
            /** Version */
            version: number;
        };
        /** KnowledgeReviewHistoryDetail */
        KnowledgeReviewHistoryDetail: {
            /** Action */
            action: string;
            /** Actorid */
            actorId: string;
            /** Decisiondigest */
            decisionDigest?: string | null;
            /** Decisionid */
            decisionId?: string | null;
            /** Itemid */
            itemId?: string | null;
            /** Occurredat */
            occurredAt: string;
            /** Reviewversion */
            reviewVersion: number;
        };
        /** KnowledgeReviewHistoryPage */
        KnowledgeReviewHistoryPage: {
            /** Items */
            items: components["schemas"]["KnowledgeReviewHistoryDetail"][];
            /** Nextcursor */
            nextCursor?: number | null;
            /** Totalcount */
            totalCount: number;
        };
        /** KnowledgeReviewInventory */
        KnowledgeReviewInventory: {
            /** Excludedcount */
            excludedCount: number;
            /** Failedcount */
            failedCount: number;
            /** Items */
            items: components["schemas"]["KnowledgeReviewInventoryItem"][];
            /** Needsreviewcount */
            needsReviewCount: number;
            /** Nextcursor */
            nextCursor?: string | null;
            /** Parsedcount */
            parsedCount: number;
            /** Totalcount */
            totalCount: number;
        };
        /** KnowledgeReviewInventoryItem */
        KnowledgeReviewInventoryItem: {
            /** Artifactdigest */
            artifactDigest: string;
            /** Attempt */
            attempt: number;
            /** Decisionactorid */
            decisionActorId?: string | null;
            /** Itemid */
            itemId: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "document" | "page" | "paragraph" | "heading" | "table" | "image" | "list" | "code" | "flow_node" | "flow_edge";
            /** Locator */
            locator: string;
            /** Reason */
            reason?: string | null;
            /** Sourcerevisionid */
            sourceRevisionId: string;
            status: components["schemas"]["ParseInventoryItemStatus"];
        };
        /** KnowledgeReviewItemComparison */
        KnowledgeReviewItemComparison: {
            extracted: components["schemas"]["KnowledgeReviewPreview"];
            /** Itemid */
            itemId: string;
            original: components["schemas"]["KnowledgeReviewPreview"];
            /** Reviewid */
            reviewId: string;
        };
        /** KnowledgeReviewItemDecisionDetail */
        KnowledgeReviewItemDecisionDetail: {
            /** Actorid */
            actorId: string;
            checkKind: components["schemas"]["KnowledgeReviewCheckKind"];
            /** Decidedat */
            decidedAt: string;
            /** Decisiondigest */
            decisionDigest: string;
            /** Decisionid */
            decisionId: string;
            /** Itemid */
            itemId: string;
            /** Note */
            note: string;
            /** Reviewversion */
            reviewVersion: number;
            status: components["schemas"]["KnowledgeReviewDecisionStatus"];
        };
        /** KnowledgeReviewOpenRequest */
        KnowledgeReviewOpenRequest: {
            /** Sourcerevisionid */
            sourceRevisionId: string;
        };
        /** KnowledgeReviewPage */
        KnowledgeReviewPage: {
            /** Items */
            items: components["schemas"]["KnowledgeReviewDetail"][];
            /** Nextcursor */
            nextCursor?: string | null;
        };
        /** KnowledgeReviewPreview */
        KnowledgeReviewPreview: {
            /**
             * Availability
             * @enum {string}
             */
            availability: "available" | "unavailable" | "unsupported";
            /** Excerpt */
            excerpt?: string | null;
            /** Reason */
            reason?: string | null;
        };
        /**
         * KnowledgeReviewStatus
         * @enum {string}
         */
        KnowledgeReviewStatus: "draft" | "checking" | "reviewing" | "approved" | "published" | "needs_review" | "expired" | "withdrawn";
        /** KnowledgeReviewSummary */
        KnowledgeReviewSummary: {
            /** Approvaldigest */
            approvalDigest: string;
            /** Expiresat */
            expiresAt: string;
            /** Reviewid */
            reviewId: string;
            /** Revieweractorid */
            reviewerActorId?: string | null;
            status: components["schemas"]["KnowledgeReviewStatus"];
            /** Version */
            version: number;
        };
        /** LiveHealth */
        LiveHealth: {
            /**
             * Status
             * @constant
             */
            status: "ok";
        };
        /** ModelCatalogItem */
        ModelCatalogItem: {
            /** Alias */
            alias: string;
            /** Capabilities */
            capabilities: ("chat" | "embed" | "structured")[];
            /** Displayname */
            displayName: string;
        };
        /** ModelCatalogPage */
        ModelCatalogPage: {
            /** Defaultalias */
            defaultAlias: string;
            /** Items */
            items: components["schemas"]["ModelCatalogItem"][];
        };
        /** OpenApiAnchor */
        OpenApiAnchor: {
            /** Jsonpointer */
            jsonPointer: string;
            /** Method */
            method: string;
            /** Path */
            path: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "openapi";
        };
        /** OpenApiAnchor */
        "OpenApiAnchor-Input": {
            /** Jsonpointer */
            jsonPointer: string;
            /** Method */
            method: string;
            /** Path */
            path: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "openapi";
        };
        /**
         * ParseInventoryItemStatus
         * @enum {string}
         */
        ParseInventoryItemStatus: "parsed" | "failed" | "needs_review" | "excluded";
        /**
         * ProblemDetails
         * @description Closed RFC 9457 error projection; only registered safe text is accepted.
         */
        ProblemDetails: {
            /** Correlationid */
            correlationId: string;
            /** Detail */
            detail: string;
            /**
             * Failurestage
             * @default null
             */
            failureStage?: ("search" | "embedding" | "answer" | "graph" | "model" | "recorder" | "execution") | null;
            /**
             * Instance
             * @default null
             */
            instance?: string | null;
            /** Retryable */
            retryable: boolean;
            /** Status */
            status: number;
            /** Title */
            title: string;
            /**
             * Type
             * @enum {string}
             */
            type: "https://tap.example/problems/answer-snapshot-unavailable" | "https://tap.example/problems/answer-unavailable" | "https://tap.example/problems/asset-revision-unavailable" | "https://tap.example/problems/association-conflict" | "https://tap.example/problems/authorization-denied" | "https://tap.example/problems/automation-mapping-required" | "https://tap.example/problems/citation-stale" | "https://tap.example/problems/citation-unavailable" | "https://tap.example/problems/conversation-integrity" | "https://tap.example/problems/conversation-not-found" | "https://tap.example/problems/document-limit-reached" | "https://tap.example/problems/document-not-found" | "https://tap.example/problems/document-not-retryable" | "https://tap.example/problems/document-state-changed" | "https://tap.example/problems/document-too-large" | "https://tap.example/problems/embedding-unavailable" | "https://tap.example/problems/empty-document" | "https://tap.example/problems/execution-provider-unavailable" | "https://tap.example/problems/graph-fact-not-found" | "https://tap.example/problems/graph-unavailable" | "https://tap.example/problems/idempotency-conflict" | "https://tap.example/problems/knowledge-projection-not-ready" | "https://tap.example/problems/knowledge-review-not-found" | "https://tap.example/problems/knowledge-review-state-conflict" | "https://tap.example/problems/knowledge-runtime-unavailable" | "https://tap.example/problems/model-not-selectable" | "https://tap.example/problems/model-unavailable" | "https://tap.example/problems/recorder-unavailable" | "https://tap.example/problems/request-validation" | "https://tap.example/problems/revision-conflict" | "https://tap.example/problems/scope-mismatch" | "https://tap.example/problems/search-execution-rejected" | "https://tap.example/problems/search-unavailable" | "https://tap.example/problems/source-command-pending" | "https://tap.example/problems/source-not-found" | "https://tap.example/problems/source-selection-required" | "https://tap.example/problems/source-unavailable" | "https://tap.example/problems/turn-not-implemented" | "https://tap.example/problems/unsupported-answer-control" | "https://tap.example/problems/unsupported-document";
        } & ({
            /** @constant */
            detail?: "The grounded answer could not be committed atomically.";
            /** @constant */
            failureStage: "answer";
            /** @constant */
            retryable?: true;
            /** @constant */
            status?: 503;
            /** @constant */
            title?: "Answer snapshot unavailable";
            /** @constant */
            type?: "https://tap.example/problems/answer-snapshot-unavailable";
        } | {
            /** @constant */
            detail?: "The answer service is currently unavailable.";
            /** @constant */
            failureStage: "answer";
            /** @constant */
            retryable?: true;
            /** @constant */
            status?: 503;
            /** @constant */
            title?: "Answer unavailable";
            /** @constant */
            type?: "https://tap.example/problems/answer-unavailable";
        } | {
            /** @constant */
            detail?: "The approved asset revision is unavailable in this Project.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 404;
            /** @constant */
            title?: "Asset revision unavailable";
            /** @constant */
            type?: "https://tap.example/problems/asset-revision-unavailable";
        } | {
            /** @constant */
            detail?: "An asset is already associated with another asset.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 409;
            /** @constant */
            title?: "Association conflict";
            /** @constant */
            type?: "https://tap.example/problems/association-conflict";
        } | {
            /** @constant */
            detail?: "The current actor and scope do not allow this operation.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 403;
            /** @constant */
            title?: "Authorization denied";
            /** @constant */
            type?: "https://tap.example/problems/authorization-denied";
        } | {
            /** @constant */
            detail?: "The published revision requires a compatible step mapping.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 409;
            /** @constant */
            title?: "Automation mapping required";
            /** @constant */
            type?: "https://tap.example/problems/automation-mapping-required";
        } | {
            /** @constant */
            detail?: "The citation no longer resolves to its exact source revision.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 404;
            /** @constant */
            title?: "Citation stale";
            /** @constant */
            type?: "https://tap.example/problems/citation-stale";
        } | {
            /** @constant */
            detail?: "The citation provider is currently unavailable.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: true;
            /** @constant */
            status?: 503;
            /** @constant */
            title?: "Citation unavailable";
            /** @constant */
            type?: "https://tap.example/problems/citation-unavailable";
        } | {
            /** @constant */
            detail?: "The Conversation has inconsistent persisted facts and cannot be served.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 500;
            /** @constant */
            title?: "Conversation integrity fault";
            /** @constant */
            type?: "https://tap.example/problems/conversation-integrity";
        } | {
            /** @constant */
            detail?: "The Conversation is unavailable in this Project.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 404;
            /** @constant */
            title?: "Conversation not found";
            /** @constant */
            type?: "https://tap.example/problems/conversation-not-found";
        } | {
            /** @constant */
            detail?: "The local knowledge space has reached its document limit.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 429;
            /** @constant */
            title?: "Document limit reached";
            /** @constant */
            type?: "https://tap.example/problems/document-limit-reached";
        } | {
            /** @constant */
            detail?: "The requested document does not exist.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 404;
            /** @constant */
            title?: "Document not found";
            /** @constant */
            type?: "https://tap.example/problems/document-not-found";
        } | {
            /** @constant */
            detail?: "Only a failed document can be retried.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 409;
            /** @constant */
            title?: "Document is not retryable";
            /** @constant */
            type?: "https://tap.example/problems/document-not-retryable";
        } | {
            /** @constant */
            detail?: "A selected document is no longer ready at its selected revision.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 409;
            /** @constant */
            title?: "Document state changed";
            /** @constant */
            type?: "https://tap.example/problems/document-state-changed";
        } | {
            /** @constant */
            detail?: "The document exceeds the 25 MiB upload limit.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 413;
            /** @constant */
            title?: "Document too large";
            /** @constant */
            type?: "https://tap.example/problems/document-too-large";
        } | {
            /** @constant */
            detail?: "The embedding service is currently unavailable.";
            /** @constant */
            failureStage: "embedding";
            /** @constant */
            retryable?: true;
            /** @constant */
            status?: 503;
            /** @constant */
            title?: "Embedding unavailable";
            /** @constant */
            type?: "https://tap.example/problems/embedding-unavailable";
        } | {
            /** @constant */
            detail?: "The document contains no processable content.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 400;
            /** @constant */
            title?: "Empty document";
            /** @constant */
            type?: "https://tap.example/problems/empty-document";
        } | {
            /** @constant */
            detail?: "The execution provider is currently unavailable.";
            /** @constant */
            failureStage: "execution";
            /** @constant */
            retryable?: true;
            /** @constant */
            status?: 503;
            /** @constant */
            title?: "Execution provider unavailable";
            /** @constant */
            type?: "https://tap.example/problems/execution-provider-unavailable";
        } | {
            /** @constant */
            detail?: "The graph fact is unavailable in this Project snapshot.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 404;
            /** @constant */
            title?: "Graph fact not found";
            /** @constant */
            type?: "https://tap.example/problems/graph-fact-not-found";
        } | {
            /** @constant */
            detail?: "The graph service is currently unavailable.";
            /** @constant */
            failureStage: "graph";
            /** @constant */
            retryable?: true;
            /** @constant */
            status?: 503;
            /** @constant */
            title?: "Graph unavailable";
            /** @constant */
            type?: "https://tap.example/problems/graph-unavailable";
        } | {
            /** @constant */
            detail?: "The idempotency key already identifies a different request.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 409;
            /** @constant */
            title?: "Idempotency conflict";
            /** @constant */
            type?: "https://tap.example/problems/idempotency-conflict";
        } | {
            /** @constant */
            detail?: "The approved knowledge projection did not validate for publication.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: true;
            /** @constant */
            status?: 409;
            /** @constant */
            title?: "Knowledge projection not ready";
            /** @constant */
            type?: "https://tap.example/problems/knowledge-projection-not-ready";
        } | {
            /** @constant */
            detail?: "The knowledge review is unavailable in this Project.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 404;
            /** @constant */
            title?: "Knowledge review not found";
            /** @constant */
            type?: "https://tap.example/problems/knowledge-review-not-found";
        } | {
            /** @constant */
            detail?: "The requested knowledge review transition is not allowed.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 409;
            /** @constant */
            title?: "Knowledge review state conflict";
            /** @constant */
            type?: "https://tap.example/problems/knowledge-review-state-conflict";
        } | {
            /** @constant */
            detail?: "The knowledge runtime is not configured.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: true;
            /** @constant */
            status?: 503;
            /** @constant */
            title?: "Knowledge runtime unavailable";
            /** @constant */
            type?: "https://tap.example/problems/knowledge-runtime-unavailable";
        } | {
            /** @constant */
            detail?: "The requested model is not available for this request.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 422;
            /** @constant */
            title?: "Model not selectable";
            /** @constant */
            type?: "https://tap.example/problems/model-not-selectable";
        } | {
            /** @constant */
            detail?: "The model service is currently unavailable.";
            /** @constant */
            failureStage: "model";
            /** @constant */
            retryable?: true;
            /** @constant */
            status?: 503;
            /** @constant */
            title?: "Model unavailable";
            /** @constant */
            type?: "https://tap.example/problems/model-unavailable";
        } | {
            /** @constant */
            detail?: "The recorder service is currently unavailable.";
            /** @constant */
            failureStage: "recorder";
            /** @constant */
            retryable?: true;
            /** @constant */
            status?: 503;
            /** @constant */
            title?: "Recorder unavailable";
            /** @constant */
            type?: "https://tap.example/problems/recorder-unavailable";
        } | {
            /** @constant */
            detail?: "The request body does not match the public API contract.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 422;
            /** @constant */
            title?: "Request validation failed";
            /** @constant */
            type?: "https://tap.example/problems/request-validation";
        } | {
            /** @constant */
            detail?: "The requested revision conflicts with the current revision.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 409;
            /** @constant */
            title?: "Revision conflict";
            /** @constant */
            type?: "https://tap.example/problems/revision-conflict";
        } | {
            /** @constant */
            detail?: "The requested project does not match the current scope.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 403;
            /** @constant */
            title?: "Scope mismatch";
            /** @constant */
            type?: "https://tap.example/problems/scope-mismatch";
        } | {
            /** @constant */
            detail?: "The search execution exceeded a safety bound.";
            /** @constant */
            failureStage: "search";
            /** @constant */
            retryable?: true;
            /** @constant */
            status?: 503;
            /** @constant */
            title?: "Search execution rejected";
            /** @constant */
            type?: "https://tap.example/problems/search-execution-rejected";
        } | {
            /** @constant */
            detail?: "The search provider is currently unavailable.";
            /** @constant */
            failureStage: "search";
            /** @constant */
            retryable?: true;
            /** @constant */
            status?: 503;
            /** @constant */
            title?: "Search unavailable";
            /** @constant */
            type?: "https://tap.example/problems/search-unavailable";
        } | {
            /** @constant */
            detail?: "The original Source command is still in progress; retry the same key.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: true;
            /** @constant */
            status?: 503;
            /** @constant */
            title?: "Source command in progress";
            /** @constant */
            type?: "https://tap.example/problems/source-command-pending";
        } | {
            /** @constant */
            detail?: "The Source is unavailable in this Project.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 404;
            /** @constant */
            title?: "Source not found";
            /** @constant */
            type?: "https://tap.example/problems/source-not-found";
        } | {
            /** @constant */
            detail?: "Select between one and twenty unique ready documents.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 400;
            /** @constant */
            title?: "Source selection required";
            /** @constant */
            type?: "https://tap.example/problems/source-selection-required";
        } | {
            /** @constant */
            detail?: "The Source is no longer available for this command.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 409;
            /** @constant */
            title?: "Source state changed";
            /** @constant */
            type?: "https://tap.example/problems/source-unavailable";
        } | {
            /** @constant */
            detail?: "The durable chat turn workflow is not available yet.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 501;
            /** @constant */
            title?: "Turn workflow not implemented";
            /** @constant */
            type?: "https://tap.example/problems/turn-not-implemented";
        } | {
            /** @constant */
            detail?: "The answer request contains a control unavailable in this demo.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 400;
            /** @constant */
            title?: "Unsupported answer control";
            /** @constant */
            type?: "https://tap.example/problems/unsupported-answer-control";
        } | {
            /** @constant */
            detail?: "The document filename, media type, or content is not supported.";
            /** @constant */
            failureStage?: unknown;
            /** @constant */
            retryable?: false;
            /** @constant */
            status?: 400;
            /** @constant */
            title?: "Unsupported document";
            /** @constant */
            type?: "https://tap.example/problems/unsupported-document";
        });
        /** PromptSuggestionItem */
        PromptSuggestionItem: {
            /** Id */
            id: string;
            /** Question */
            question: string;
            /** Sources */
            sources: components["schemas"]["PromptSuggestionSource"][];
        };
        /** PromptSuggestionPage */
        PromptSuggestionPage: {
            /** Items */
            items: components["schemas"]["PromptSuggestionItem"][];
        };
        /** PromptSuggestionSource */
        PromptSuggestionSource: {
            /** Name */
            name: string;
            /** Sourceid */
            sourceId: string;
        };
        /** PublishedKnowledgeSource */
        PublishedKnowledgeSource: {
            /** Approveditemcount */
            approvedItemCount: number;
            /** Documentid */
            documentId: string;
            /** Expiresat */
            expiresAt?: string | null;
            /** Filename */
            filename: string;
            /** Inventoryitemcount */
            inventoryItemCount: number;
            /** Partial */
            partial: boolean;
            /** Publicationid */
            publicationId?: string | null;
            /** Revisionid */
            revisionId: string;
            /** Sourceid */
            sourceId: string;
            /** Sourcename */
            sourceName: string;
        };
        /** PublishedKnowledgeSourcePage */
        PublishedKnowledgeSourcePage: {
            /** Items */
            items: components["schemas"]["PublishedKnowledgeSource"][];
        };
        /** QueryPlanReadyEvent */
        QueryPlanReadyEvent: {
            payload: components["schemas"]["QueryPlanReadyPayload"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "query.plan_ready";
        };
        /** QueryPlanReadyPayload */
        QueryPlanReadyPayload: {
            answerMode: components["schemas"]["AnswerMode"];
            /** Queryplanid */
            queryPlanId: string;
            /** Sourcefamilies */
            sourceFamilies: components["schemas"]["SourceFamily"][];
        };
        /** ReadyHealth */
        ReadyHealth: {
            /** Components */
            components: components["schemas"]["HealthComponent"][];
            /**
             * Status
             * @enum {string}
             */
            status: "ready" | "unready";
        };
        /** RerankCompletedEvent */
        RerankCompletedEvent: {
            payload: components["schemas"]["RerankCompletedPayload"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "rerank.completed";
        };
        /** RerankCompletedPayload */
        RerankCompletedPayload: {
            /** Candidatecount */
            candidateCount: number;
            /** Durationms */
            durationMs: number;
        };
        /**
         * ResourceMode
         * @enum {string}
         */
        ResourceMode: "required" | "preferred" | "scope";
        /**
         * ResourceRef
         * @description Browser-provided retrieval intent; it cannot contain policy or ACL facts.
         */
        ResourceRef: {
            /** @default null */
            anchor?: components["schemas"]["StructuralAnchor"] | null;
            family: components["schemas"]["SourceFamily"];
            /** @default preferred */
            mode?: components["schemas"]["ResourceMode"];
            /**
             * Requestedrevision
             * @default null
             */
            requestedRevision?: string | null;
            /** Sourceid */
            sourceId: string;
        };
        /**
         * RetrievalAnswerRequest
         * @description Grounded-answer intent with the same narrowing-only search fields.
         */
        RetrievalAnswerRequest: {
            /** @default quick */
            answerMode?: components["schemas"]["AnswerMode"];
            /** Query */
            query: string;
            /**
             * Requestedcorpusversion
             * @default null
             */
            requestedCorpusVersion?: string | null;
            /**
             * Requestedenvironment
             * @default null
             */
            requestedEnvironment?: string | null;
            /**
             * Resourcerefs
             * @default null
             */
            resourceRefs?: components["schemas"]["ResourceRef"][] | null;
            /**
             * Sources
             * @default null
             */
            sources?: components["schemas"]["SourceFamily"][] | null;
            /**
             * Topk
             * @default null
             */
            topK?: number | null;
        };
        /** RetrievalAnswerResponse */
        RetrievalAnswerResponse: {
            /** Abstained */
            abstained: boolean;
            /** @default null */
            abstentionReason?: components["schemas"]["AbstentionReason"] | null;
            /** Answer */
            answer: string;
            /** Citations */
            citations: components["schemas"]["RetrievalCitation"][];
            /** Claims */
            claims: components["schemas"]["RetrievalClaim"][];
            /** Contextsnapshotid */
            contextSnapshotId: string;
            /** Corpusversion */
            corpusVersion: string;
            /**
             * Degradationreasons
             * @default null
             */
            degradationReasons?: string[] | null;
            /** Degradedmode */
            degradedMode: boolean;
            /**
             * Graphcontextstatus
             * @default NOT_SELECTED
             * @enum {string}
             */
            graphContextStatus?: "APPLIED" | "NOT_READY" | "FAILED" | "UNAVAILABLE" | "NOT_SELECTED";
            /**
             * Graphsnapshotid
             * @default null
             */
            graphSnapshotId?: string | null;
            /** Queryplanid */
            queryPlanId: string;
            /** Retrievalprofileid */
            retrievalProfileId: string;
            /** Traceid */
            traceId: string;
        };
        /** RetrievalCitation */
        RetrievalCitation: {
            /**
             * Approvaldigest
             * @default null
             */
            approvalDigest?: string | null;
            /**
             * Approveditemid
             * @default null
             */
            approvedItemId?: string | null;
            /** Chunkcontenthash */
            chunkContentHash: string;
            /** Chunkid */
            chunkId: string;
            /** Citationid */
            citationId: string;
            contentRole: components["schemas"]["ContentRole"];
            /**
             * Derivedfromchunkids
             * @default null
             */
            derivedFromChunkIds?: string[] | null;
            /** Evidencelabel */
            evidenceLabel: string;
            /** Logicalchunkid */
            logicalChunkId: string;
            /**
             * Publicationid
             * @default null
             */
            publicationId?: string | null;
            source: components["schemas"]["RetrievalSourceRevision"];
        };
        /** RetrievalClaim */
        RetrievalClaim: {
            /** Answerend */
            answerEnd: number;
            /** Answerstart */
            answerStart: number;
            /** Citationids */
            citationIds: string[];
            /** Claimid */
            claimId: string;
            /** Text */
            text: string;
        };
        /** RetrievalHit */
        RetrievalHit: {
            /** Acldecisionid */
            aclDecisionId: string;
            /**
             * Approvaldigest
             * @default null
             */
            approvalDigest?: string | null;
            /**
             * Approveditemid
             * @default null
             */
            approvedItemId?: string | null;
            /** Chunkcontenthash */
            chunkContentHash: string;
            /** Chunkid */
            chunkId: string;
            /** Citationid */
            citationId: string;
            /** Content */
            content: string;
            contentRole: components["schemas"]["ContentRole"];
            /** Embeddingmodelversion */
            embeddingModelVersion: string;
            /** Evidencelabel */
            evidenceLabel: string;
            indexFamily: components["schemas"]["SourceFamily"];
            /** Logicalchunkid */
            logicalChunkId: string;
            /**
             * Publicationid
             * @default null
             */
            publicationId?: string | null;
            /** Schemaversion */
            schemaVersion: string;
            scores: components["schemas"]["RetrievalScores"];
            source: components["schemas"]["RetrievalSourceRevision"];
            /**
             * Title
             * @default null
             */
            title?: string | null;
        };
        /** RetrievalHitsReadyEvent */
        RetrievalHitsReadyEvent: {
            payload: components["schemas"]["RetrievalHitsReadyPayload"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "retrieval.hits_ready";
        };
        /** RetrievalHitsReadyPayload */
        RetrievalHitsReadyPayload: {
            /** Authorizedhitcount */
            authorizedHitCount: number;
            /** Traceid */
            traceId: string;
        };
        /** RetrievalScores */
        RetrievalScores: {
            /**
             * Bm25
             * @default null
             */
            bm25?: number | null;
            /**
             * Exact
             * @default null
             */
            exact?: number | null;
            /**
             * Rerank
             * @default null
             */
            rerank?: number | null;
            /**
             * Rrf
             * @default null
             */
            rrf?: number | null;
            /**
             * Vector
             * @default null
             */
            vector?: number | null;
        };
        /**
         * RetrievalSearchRequest
         * @description Browser-visible retrieval intent; all authoritative scope is omitted.
         */
        RetrievalSearchRequest: {
            /** @default quick */
            answerMode?: components["schemas"]["AnswerMode"];
            /** Query */
            query: string;
            /**
             * Requestedcorpusversion
             * @default null
             */
            requestedCorpusVersion?: string | null;
            /**
             * Requestedenvironment
             * @default null
             */
            requestedEnvironment?: string | null;
            /**
             * Resourcerefs
             * @default null
             */
            resourceRefs?: components["schemas"]["ResourceRef"][] | null;
            /**
             * Sources
             * @default null
             */
            sources?: components["schemas"]["SourceFamily"][] | null;
            /**
             * Topk
             * @default null
             */
            topK?: number | null;
        };
        /** RetrievalSearchResponse */
        RetrievalSearchResponse: {
            /** Contextsnapshotid */
            contextSnapshotId: string;
            /** Corpusversion */
            corpusVersion: string;
            /**
             * Degradationreasons
             * @default null
             */
            degradationReasons?: string[] | null;
            /** Degradedmode */
            degradedMode: boolean;
            /** Hits */
            hits: components["schemas"]["RetrievalHit"][];
            /** Queryplanid */
            queryPlanId: string;
            /** Retrievalprofileid */
            retrievalProfileId: string;
            /** Traceid */
            traceId: string;
        };
        /** RetrievalSourceRevision */
        RetrievalSourceRevision: {
            anchor: components["schemas"]["StructuralAnchor"];
            /** Revision */
            revision: string;
            revisionKind: components["schemas"]["RevisionKind"];
            /** Sourcecontenthash */
            sourceContentHash: string;
            /** Sourceid */
            sourceId: string;
            /** Sourcetype */
            sourceType: string;
        };
        /**
         * RevisionKind
         * @enum {string}
         */
        RevisionKind: "git_commit" | "blob_version" | "mysql_version";
        /**
         * RuntimeMode
         * @description Server-owned validation context; never a personal authentication claim.
         */
        RuntimeMode: {
            /** Actorid */
            actorId: string;
            /**
             * Identitymode
             * @constant
             */
            identityMode: "validation";
            /**
             * Mode
             * @constant
             */
            mode: "validation";
            /** Projectid */
            projectId: string;
        };
        /** SkillRevisionPage */
        SkillRevisionPage: {
            /** Items */
            items: components["schemas"]["SkillRevisionSummary"][];
        };
        /** SkillRevisionSummary */
        SkillRevisionSummary: {
            /** Applicabletasks */
            applicableTasks: ("knowledge.answer" | "test-plan.generate" | "automation.generate")[];
            /** Assetid */
            assetId: string;
            /** Contentdigest */
            contentDigest: string;
            /** Displayname */
            displayName: string;
            /** Revisionid */
            revisionId: string;
        };
        /** SourceAccepted */
        SourceAccepted: {
            accepted: components["schemas"]["DocumentAccepted"];
            source: components["schemas"]["SourceSummary"];
        };
        /** SourceDetail */
        SourceDetail: {
            /** Createdat */
            createdAt: string;
            /** Documentcount */
            documentCount: number;
            documents: components["schemas"]["SourceDocumentPage"];
            /** Failedcount */
            failedCount: number;
            /** Name */
            name: string;
            /** Readycount */
            readyCount: number;
            /** Sourceid */
            sourceId: string;
        };
        /** SourceDocument */
        SourceDocument: {
            /** Attempt */
            attempt: number;
            /** Chunkcount */
            chunkCount: number;
            /** Documentid */
            documentId: string;
            /** Errorcode */
            errorCode?: string | null;
            /** Errorsummary */
            errorSummary?: string | null;
            /** Filename */
            filename: string;
            /**
             * Mediatype
             * @enum {string}
             */
            mediaType: "application/pdf" | "application/vnd.openxmlformats-officedocument.wordprocessingml.document" | "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" | "text/markdown" | "text/plain" | "image/png" | "image/jpeg";
            /** Normalizedpreview */
            normalizedPreview?: string | null;
            /** Revisionid */
            revisionId: string;
            /** Sourcecontenthash */
            sourceContentHash: string;
            /** Sourceid */
            sourceId: string;
            stage: components["schemas"]["IngestionStage"];
            /** Stages */
            stages: components["schemas"]["DocumentStageSnapshot"][];
            status: components["schemas"]["DocumentStatus"];
            /** Updatedat */
            updatedAt: string;
        };
        /** SourceDocumentPage */
        SourceDocumentPage: {
            /** Items */
            items: components["schemas"]["SourceDocument"][];
            /** Nextcursor */
            nextCursor?: string | null;
        };
        /**
         * SourceFamily
         * @enum {string}
         */
        SourceFamily: "doc" | "code" | "bdd" | "failure";
        /** SourcePage */
        SourcePage: {
            /** Items */
            items: components["schemas"]["SourceSummary"][];
            /** Nextcursor */
            nextCursor?: string | null;
        };
        /** SourceRetryRequest */
        SourceRetryRequest: {
            /** Documentid */
            documentId: string;
            /** Expectedattempt */
            expectedAttempt: number;
            /** Revisionid */
            revisionId: string;
        };
        /** SourceRevisionRef */
        SourceRevisionRef: {
            anchor: components["schemas"]["tap__contracts__chat_stream__StructuralAnchor"];
            /** Revision */
            revision: string;
            revisionKind: components["schemas"]["RevisionKind"];
            /** Sourcecontenthash */
            sourceContentHash: string;
            /** Sourceid */
            sourceId: string;
            /** Sourcetype */
            sourceType: string;
        };
        /** SourceSummary */
        SourceSummary: {
            /** Createdat */
            createdAt: string;
            /** Documentcount */
            documentCount: number;
            /** Failedcount */
            failedCount: number;
            /** Name */
            name: string;
            /** Readycount */
            readyCount: number;
            /** Sourceid */
            sourceId: string;
        };
        /** StageCompletedEvent */
        StageCompletedEvent: {
            payload: components["schemas"]["StageCompletedPayload"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "stage.completed";
        };
        /** StageCompletedPayload */
        StageCompletedPayload: {
            /** Durationms */
            durationMs: number;
            /** Stage */
            stage: string;
        };
        /** StageStartedEvent */
        StageStartedEvent: {
            payload: components["schemas"]["StageStartedPayload"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "stage.started";
        };
        /** StageStartedPayload */
        StageStartedPayload: {
            /** Stage */
            stage: string;
        };
        /**
         * StructuralAnchor
         * @description A closed, structural location inside one authorized source family.
         */
        StructuralAnchor: components["schemas"]["DocumentAnchor"] | components["schemas"]["CodeAnchor"] | components["schemas"]["BddAnchor"] | components["schemas"]["OpenApiAnchor"] | components["schemas"]["FailureAnchor"];
        /**
         * StructuralAnchor
         * @description A closed, structural location inside one authorized source family.
         */
        "StructuralAnchor-Input": components["schemas"]["DocumentAnchor-Input"] | components["schemas"]["CodeAnchor-Input"] | components["schemas"]["BddAnchor-Input"] | components["schemas"]["OpenApiAnchor-Input"] | components["schemas"]["FailureAnchor-Input"];
        /** TestPlanCaseView */
        "TestPlanCaseView-Input": {
            /** Caseid */
            caseId: string;
            /**
             * Coveredrequirementids
             * @default []
             */
            coveredRequirementIds?: string[];
            /** Critical */
            critical: boolean;
            /** Objective */
            objective: string;
            /** Ordinal */
            ordinal: number;
            /** Scenarios */
            scenarios: components["schemas"]["TestPlanScenarioView"][];
            /** Title */
            title: string;
        };
        /** TestPlanCaseView */
        "TestPlanCaseView-Output": {
            /** Caseid */
            caseId: string;
            /**
             * Coveredrequirementids
             * @default []
             */
            coveredRequirementIds?: string[];
            /** Critical */
            critical: boolean;
            /** Objective */
            objective: string;
            /** Ordinal */
            ordinal: number;
            /** Scenarios */
            scenarios: components["schemas"]["TestPlanScenarioView"][];
            /** Title */
            title: string;
        };
        /** TestPlanCitationView */
        TestPlanCitationView: {
            /** Anchor */
            anchor?: {
                [key: string]: unknown;
            } | null;
            /** Chunkid */
            chunkId: string;
            /** Citationid */
            citationId: string;
            /** Claimtext */
            claimText: string;
            /** Contentdigest */
            contentDigest: string;
            /** Documentrevisionid */
            documentRevisionId: string;
            /** Evidencepreviewurl */
            evidencePreviewUrl?: string | null;
            /**
             * Origin
             * @enum {string}
             */
            origin: "SOURCE" | "GRAPH_EXTRACTED";
            /** Sourcerevisionid */
            sourceRevisionId: string;
        };
        /** TestPlanCoverageGapView */
        TestPlanCoverageGapView: {
            /** Gapid */
            gapId: string;
            /** Reason */
            reason: string;
            /** Requirementref */
            requirementRef: string;
            /**
             * Severity
             * @enum {string}
             */
            severity: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
        };
        /** TestPlanEvidencePreview */
        TestPlanEvidencePreview: {
            /** Anchor */
            anchor: {
                [key: string]: unknown;
            };
            /** Chunkid */
            chunkId: string;
            /** Citationid */
            citationId: string;
            /** Claimtext */
            claimText: string;
            /** Contentdigest */
            contentDigest: string;
            /** Documentrevisionid */
            documentRevisionId: string;
            /**
             * Origin
             * @enum {string}
             */
            origin: "SOURCE" | "GRAPH_EXTRACTED" | "GRAPH_INFERRED";
            /** Sourcerevisionid */
            sourceRevisionId: string;
        };
        /** TestPlanGenerationAccepted */
        TestPlanGenerationAccepted: {
            /** Deeplink */
            deepLink: string;
            /** Failurecode */
            failureCode?: string | null;
            /** Jobid */
            jobId: string;
            /**
             * Progress
             * @enum {string}
             */
            progress: "queued" | "running" | "waiting" | "completed" | "failed" | "canceled";
            /** Revisionid */
            revisionId: string;
            /** Rowversion */
            rowVersion: number;
            /**
             * Status
             * @enum {string}
             */
            status: "PENDING" | "RUNNING" | "WAITING" | "DRAFT_READY" | "FAILED" | "CANCELED";
            /** Testplanid */
            testPlanId: string;
        };
        /** TestPlanGenerationCanceledEvent */
        TestPlanGenerationCanceledEvent: {
            payload: components["schemas"]["TestPlanGenerationCanceledPayload"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "test-plan.generation.canceled";
        };
        /** TestPlanGenerationCanceledPayload */
        TestPlanGenerationCanceledPayload: {
            /** Jobid */
            jobId: string;
            /** Reason */
            reason: string;
        };
        /** TestPlanGenerationFailedEvent */
        TestPlanGenerationFailedEvent: {
            payload: components["schemas"]["TestPlanGenerationFailedPayload"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "test-plan.generation.failed";
        };
        /** TestPlanGenerationFailedPayload */
        TestPlanGenerationFailedPayload: {
            /** Failurecode */
            failureCode: string;
            /** Jobid */
            jobId: string;
        };
        /** TestPlanGenerationRequestBody */
        TestPlanGenerationRequestBody: {
            /** Conversationid */
            conversationId: string;
            /** Objective */
            objective: string;
            /** Turnid */
            turnId: string;
        };
        /** TestPlanGenerationResultEvent */
        TestPlanGenerationResultEvent: {
            payload: components["schemas"]["TestPlanGenerationResultPayload"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "test-plan.generation.result_ready";
        };
        /** TestPlanGenerationResultPayload */
        TestPlanGenerationResultPayload: {
            /** Deeplink */
            deepLink: string;
            /** Jobid */
            jobId: string;
            /** Revisionid */
            revisionId: string;
            /** Testplanid */
            testPlanId: string;
        };
        /** TestPlanGenerationWaitingEvent */
        TestPlanGenerationWaitingEvent: {
            payload: components["schemas"]["TestPlanGenerationWaitingPayload"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "test-plan.generation.waiting";
        };
        /** TestPlanGenerationWaitingPayload */
        TestPlanGenerationWaitingPayload: {
            /** Jobid */
            jobId: string;
            /** Reason */
            reason: string;
        };
        /** TestPlanReviewDecisionView */
        TestPlanReviewDecisionView: {
            /** Actorid */
            actorId: string;
            /** Createdat */
            createdAt?: string | null;
            /** Decisionid */
            decisionId: string;
            /**
             * Disposition
             * @enum {string}
             */
            disposition: "PENDING" | "ACCEPTED_UNCHANGED" | "ACCEPTED_MODIFIED" | "REJECTED";
            /** Reason */
            reason: string;
            /** Reviewedcontentdigest */
            reviewedContentDigest: string;
        };
        /** TestPlanReviewRequest */
        TestPlanReviewRequest: {
            /**
             * Disposition
             * @enum {string}
             */
            disposition: "PENDING" | "ACCEPTED_UNCHANGED" | "ACCEPTED_MODIFIED" | "REJECTED";
            /** Reason */
            reason: string;
        };
        /** TestPlanReviewSummaryView */
        TestPlanReviewSummaryView: {
            /** Modifiedcount */
            modifiedCount: number;
            /** Rejectedcount */
            rejectedCount: number;
            /** Reviewedcount */
            reviewedCount: number;
            /** Totaladoptionrate */
            totalAdoptionRate: number | null;
            /** Unchangedadoptionrate */
            unchangedAdoptionRate: number | null;
            /** Unchangedcount */
            unchangedCount: number;
        };
        /** TestPlanRevisionPage */
        TestPlanRevisionPage: {
            /** Items */
            items: components["schemas"]["TestPlanRevisionView"][];
        };
        /** TestPlanRevisionUpdate */
        TestPlanRevisionUpdate: {
            /** Assumptions */
            assumptions: components["schemas"]["TestPlanTextFactView"][];
            /** Cases */
            cases: components["schemas"]["TestPlanCaseView-Input"][];
            /** Citations */
            citations: components["schemas"]["TestPlanCitationView"][];
            /** Coveragegaps */
            coverageGaps: components["schemas"]["TestPlanCoverageGapView"][];
            /** Objective */
            objective: string;
            /** Prerequisites */
            prerequisites: string[];
            /** Risks */
            risks: string[];
            /** Scopeitems */
            scopeItems: string[];
            /** Title */
            title: string;
            /** Unknowns */
            unknowns: components["schemas"]["TestPlanTextFactView"][];
        };
        /** TestPlanRevisionView */
        TestPlanRevisionView: {
            /** Adoptedfromrevisionid */
            adoptedFromRevisionId?: string | null;
            /** Agentrevisionid */
            agentRevisionId?: string | null;
            /**
             * Approvedknowledgerevisionids
             * @default []
             */
            approvedKnowledgeRevisionIds?: string[];
            /** Assumptions */
            assumptions: components["schemas"]["TestPlanTextFactView"][];
            /** Authoractorid */
            authorActorId?: string | null;
            /** Cases */
            cases: components["schemas"]["TestPlanCaseView-Output"][];
            /** Citations */
            citations: components["schemas"]["TestPlanCitationView"][];
            /** Contentdigest */
            contentDigest: string;
            /**
             * Coveragedenominator
             * @default 0
             */
            coverageDenominator?: number;
            /** Coveragegaps */
            coverageGaps: components["schemas"]["TestPlanCoverageGapView"][];
            /**
             * Coveredrequirementcount
             * @default 0
             */
            coveredRequirementCount?: number;
            /**
             * Coveredrequirementids
             * @default []
             */
            coveredRequirementIds?: string[];
            /** Deeplink */
            deepLink: string;
            /** Generatedcontentdigest */
            generatedContentDigest?: string | null;
            /** Modelrevisionid */
            modelRevisionId?: string | null;
            /**
             * Needsreview
             * @default false
             */
            needsReview?: boolean;
            /** Needsreviewreason */
            needsReviewReason?: string | null;
            /** Objective */
            objective: string;
            /**
             * Origin
             * @enum {string}
             */
            origin: "VALIDATION" | "PRODUCT";
            /** Prerequisites */
            prerequisites: string[];
            /**
             * Requirementids
             * @default []
             */
            requirementIds?: string[];
            /** Requirementscopedigest */
            requirementScopeDigest?: string | null;
            /** Requirementscopeid */
            requirementScopeId?: string | null;
            /** Requirementscopeversion */
            requirementScopeVersion?: number | null;
            /**
             * Reviewdecisions
             * @default []
             */
            reviewDecisions?: components["schemas"]["TestPlanReviewDecisionView"][];
            /** Revisionid */
            revisionId: string;
            /** Risks */
            risks: string[];
            /** Rowversion */
            rowVersion: number;
            /** Scopeitems */
            scopeItems: string[];
            /**
             * Skillrevisionids
             * @default []
             */
            skillRevisionIds?: string[];
            /**
             * Status
             * @enum {string}
             */
            status: "DRAFT" | "VALIDATING" | "PUBLISHED" | "SUPERSEDED";
            /**
             * Strictreviewrequired
             * @default false
             */
            strictReviewRequired?: boolean;
            /** Testplanid */
            testPlanId: string;
            /** Title */
            title: string;
            /** Unknowns */
            unknowns: components["schemas"]["TestPlanTextFactView"][];
            /** Validationdigest */
            validationDigest?: string | null;
            /** Version */
            version: number;
        };
        /** TestPlanScenarioView */
        TestPlanScenarioView: {
            /** Ordinal */
            ordinal: number;
            /** Scenarioid */
            scenarioId: string;
            /** Steps */
            steps: components["schemas"]["TestPlanStepView"][];
            /** Title */
            title: string;
        };
        /** TestPlanStepView */
        TestPlanStepView: {
            /**
             * Citationids
             * @default []
             */
            citationIds?: string[];
            /** Critical */
            critical: boolean;
            /** Expectedresult */
            expectedResult?: string | null;
            /**
             * Keyword
             * @enum {string}
             */
            keyword: "Given" | "When" | "Then" | "And" | "But";
            /** Ordinal */
            ordinal: number;
            /** Stepid */
            stepId: string;
            /** Text */
            text: string;
            /**
             * Unknownids
             * @default []
             */
            unknownIds?: string[];
        };
        /** TestPlanTextFactView */
        TestPlanTextFactView: {
            /** Factid */
            factId: string;
            /** Graphedgeid */
            graphEdgeId?: string | null;
            /** Requirementref */
            requirementRef?: string | null;
            /** Text */
            text: string;
        };
        /** TurnAbstainedEvent */
        TurnAbstainedEvent: {
            payload: components["schemas"]["TurnAbstainedPayload"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "turn.abstained";
        };
        /** TurnAbstainedPayload */
        TurnAbstainedPayload: {
            answer: components["schemas"]["tap__contracts__chat_stream__RetrievalAnswerResponse"];
        };
        /** TurnCanceledEvent */
        TurnCanceledEvent: {
            payload: components["schemas"]["TurnCanceledPayload"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "turn.canceled";
        };
        /** TurnCanceledPayload */
        TurnCanceledPayload: {
            /** Partialanswerretained */
            partialAnswerRetained: boolean;
        };
        /** TurnCompletedEvent */
        TurnCompletedEvent: {
            payload: components["schemas"]["TurnCompletedPayload"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "turn.completed";
        };
        /** TurnCompletedPayload */
        TurnCompletedPayload: {
            answer?: components["schemas"]["tap__contracts__chat_stream__RetrievalAnswerResponse"] | null;
            /** State */
            state?: "completed" | null;
        };
        /** TurnDegradedEvent */
        TurnDegradedEvent: {
            payload: components["schemas"]["TurnDegradedPayload"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "turn.degraded";
        };
        /** TurnDegradedPayload */
        TurnDegradedPayload: {
            /** Availablestages */
            availableStages: string[];
            /** Reason */
            reason: string;
        };
        /** TurnFailedEvent */
        TurnFailedEvent: {
            payload: components["schemas"]["TurnFailedPayload"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "turn.failed";
        };
        /** TurnFailedPayload */
        TurnFailedPayload: {
            problem: components["schemas"]["ProblemDetails"];
        };
        /** TurnStartedEvent */
        TurnStartedEvent: {
            payload: components["schemas"]["TurnStartedPayload"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "turn.started";
        };
        /** TurnStartedPayload */
        TurnStartedPayload: {
            /**
             * State
             * @constant
             */
            state: "running";
        };
        /** ValidationError */
        ValidationError: {
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        /** BddAnchor */
        tap__contracts__chat_stream__BddAnchor: {
            /** Featureid */
            featureId: string;
            /** Scenarioid */
            scenarioId?: string | null;
            /** Stepid */
            stepId?: string | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "bdd";
        };
        /** CodeAnchor */
        tap__contracts__chat_stream__CodeAnchor: {
            /** Lineend */
            lineEnd: number;
            /** Linestart */
            lineStart: number;
            /** Path */
            path: string;
            /** Repo */
            repo: string;
            /** Symbol */
            symbol?: string | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "code";
        };
        /** DocumentAnchor */
        tap__contracts__chat_stream__DocumentAnchor: {
            /** Bbox */
            bbox?: number[] | null;
            /** Endoffset */
            endOffset?: number | null;
            /** Headingpath */
            headingPath?: string[] | null;
            /** Inventoryitemid */
            inventoryItemId?: string | null;
            /** Page */
            page?: number | null;
            /** Startoffset */
            startOffset?: number | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "document";
        };
        /** FailureAnchor */
        tap__contracts__chat_stream__FailureAnchor: {
            /** Incidentid */
            incidentId: string;
            /** Runid */
            runId?: string | null;
            /** Timeend */
            timeEnd?: string | null;
            /** Timestart */
            timeStart?: string | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "failure";
        };
        /** OpenApiAnchor */
        tap__contracts__chat_stream__OpenApiAnchor: {
            /** Jsonpointer */
            jsonPointer: string;
            /** Method */
            method: string;
            /** Path */
            path: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "openapi";
        };
        /** RetrievalAnswerResponse */
        tap__contracts__chat_stream__RetrievalAnswerResponse: {
            /** Abstained */
            abstained: boolean;
            abstentionReason?: components["schemas"]["AbstentionReason"] | null;
            /** Answer */
            answer: string;
            /** Citations */
            citations: components["schemas"]["Citation"][];
            /** Claims */
            claims: components["schemas"]["AnswerClaim"][];
            /** Contextsnapshotid */
            contextSnapshotId: string;
            /** Corpusversion */
            corpusVersion: string;
            /** Degradationreasons */
            degradationReasons?: string[] | null;
            /** Degradedmode */
            degradedMode: boolean;
            /**
             * Graphcontextstatus
             * @default NOT_SELECTED
             * @enum {string}
             */
            graphContextStatus?: "APPLIED" | "NOT_READY" | "FAILED" | "UNAVAILABLE" | "NOT_SELECTED";
            /** Graphsnapshotid */
            graphSnapshotId?: string | null;
            /** Queryplanid */
            queryPlanId: string;
            /** Retrievalprofileid */
            retrievalProfileId: string;
            /** Traceid */
            traceId: string;
        };
        /**
         * StructuralAnchor
         * @description A closed source location retained in a browser-visible citation.
         */
        tap__contracts__chat_stream__StructuralAnchor: components["schemas"]["tap__contracts__chat_stream__DocumentAnchor"] | components["schemas"]["tap__contracts__chat_stream__CodeAnchor"] | components["schemas"]["tap__contracts__chat_stream__BddAnchor"] | components["schemas"]["tap__contracts__chat_stream__OpenApiAnchor"] | components["schemas"]["tap__contracts__chat_stream__FailureAnchor"];
        /** BddAnchor */
        tap__contracts__http__BddAnchor: {
            /** Featureid */
            featureId: string;
            /** Scenarioid */
            scenarioId?: string | null;
            /** Stepid */
            stepId?: string | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "bdd";
        };
        /** CodeAnchor */
        tap__contracts__http__CodeAnchor: {
            /** Lineend */
            lineEnd: number;
            /** Linestart */
            lineStart: number;
            /** Path */
            path: string;
            /** Repo */
            repo: string;
            /** Symbol */
            symbol?: string | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "code";
        };
        /** DocumentAnchor */
        tap__contracts__http__DocumentAnchor: {
            /** Bbox */
            bbox?: number[] | null;
            /** Endoffset */
            endOffset?: number | null;
            /** Headingpath */
            headingPath?: string[] | null;
            /** Inventoryitemid */
            inventoryItemId?: string | null;
            /** Page */
            page?: number | null;
            /** Startoffset */
            startOffset?: number | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "document";
        };
        /** FailureAnchor */
        tap__contracts__http__FailureAnchor: {
            /** Incidentid */
            incidentId: string;
            /** Runid */
            runId?: string | null;
            /** Timeend */
            timeEnd?: string | null;
            /** Timestart */
            timeStart?: string | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "failure";
        };
        /** OpenApiAnchor */
        tap__contracts__http__OpenApiAnchor: {
            /** Jsonpointer */
            jsonPointer: string;
            /** Method */
            method: string;
            /** Path */
            path: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "openapi";
        };
        /** RetrievalAnswerResponse */
        tap__contracts__http__RetrievalAnswerResponse: {
            /** Abstained */
            abstained: boolean;
            abstentionReason?: components["schemas"]["AbstentionReason"] | null;
            /** Answer */
            answer: string;
            /** Citations */
            citations: components["schemas"]["RetrievalCitation"][];
            /** Claims */
            claims: components["schemas"]["RetrievalClaim"][];
            /** Contextsnapshotid */
            contextSnapshotId: string;
            /** Corpusversion */
            corpusVersion: string;
            /** Degradationreasons */
            degradationReasons?: string[] | null;
            /** Degradedmode */
            degradedMode: boolean;
            /**
             * Graphcontextstatus
             * @default NOT_SELECTED
             * @enum {string}
             */
            graphContextStatus?: "APPLIED" | "NOT_READY" | "FAILED" | "UNAVAILABLE" | "NOT_SELECTED";
            /** Graphsnapshotid */
            graphSnapshotId?: string | null;
            /** Queryplanid */
            queryPlanId: string;
            /** Retrievalprofileid */
            retrievalProfileId: string;
            /** Traceid */
            traceId: string;
        };
        /**
         * StructuralAnchor
         * @description A closed, structural location inside one authorized source family.
         */
        "tap__contracts__http__StructuralAnchor-Output": components["schemas"]["tap__contracts__http__DocumentAnchor"] | components["schemas"]["tap__contracts__http__CodeAnchor"] | components["schemas"]["tap__contracts__http__BddAnchor"] | components["schemas"]["tap__contracts__http__OpenApiAnchor"] | components["schemas"]["tap__contracts__http__FailureAnchor"];
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    ai_list_agent_revisions: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AiAgentRevisionPage"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge runtime unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    ai_get_agent_revision: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                revision_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AiAgentRevisionSummary"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Agent revision unavailable */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge runtime unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    ai_list_models: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ModelCatalogPage"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Model catalog unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    ai_list_skill_revisions: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SkillRevisionPage"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge runtime unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    ai_get_skill_revision: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                revision_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SkillRevisionSummary"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Skill revision unavailable */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge runtime unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    conversation_list: {
        parameters: {
            query?: {
                limit?: number;
                cursor?: string | null;
                /** @description Case-insensitive title substring; surrounding whitespace is ignored. */
                q?: string | null;
            };
            header?: never;
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConversationPage"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Invalid cursor */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    conversation_create: {
        parameters: {
            query?: never;
            header: {
                "idempotency-key": string;
            };
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ConversationCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConversationAccepted"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Approved revision unavailable */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Idempotency conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Request validation failed or model not selectable */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Runtime or model catalog unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    conversation_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                conversation_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConversationDetail"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Conversation not found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    conversation_delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                conversation_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Only the creating actor may delete */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Conversation not found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    conversation_rename: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                conversation_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ConversationRenameRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConversationSummary"];
                };
            };
            /** @description Only the creating actor may rename */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Conversation not found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    conversation_list_events: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                conversation_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConversationEventPage"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Conversation not found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    conversation_stream: {
        parameters: {
            query?: never;
            header?: {
                "Last-Event-ID"?: string | null;
            };
            path: {
                conversation_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Recoverable conversation event stream */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/event-stream": components["schemas"]["ChatEventEnvelope"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Conversation not found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Invalid Last-Event-ID */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    conversation_append_turn: {
        parameters: {
            query?: never;
            header: {
                "idempotency-key": string;
            };
            path: {
                conversation_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ConversationCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConversationAccepted"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Conversation or approved revision not found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Idempotency conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Request validation failed or model not selectable */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Runtime or model catalog unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    conversation_cancel_turn: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                conversation_id: string;
                turn_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConversationTurnSummary"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Conversation or Turn not found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    conversation_get_citation: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                conversation_id: string;
                turn_id: string;
                citation_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CitationPreview"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Conversation Turn citation not found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Citation evidence is stale */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Citation evidence unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    insights_explain_report: {
        parameters: {
            query?: never;
            header: {
                "idempotency-key": string;
            };
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["InsightsExplanationRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InsightsExplanationAccepted"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    insights_get_explanation: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                conversation_id: string;
                turn_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InsightsExplanationResult"] | components["schemas"]["InsightsExplanationAccepted"];
                };
            };
            /** @description Accepted */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InsightsExplanationAccepted"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    knowledge_create_answer: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RetrievalAnswerRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["tap__contracts__http__RetrievalAnswerResponse"];
                };
            };
            /** @description Invalid answer selection */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Document state changed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Invalid answer request */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge runtime unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    preview_upload_api_v1_projects__project_id__knowledge_chunks_preview_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "multipart/form-data": components["schemas"]["Body_preview_upload_api_v1_projects__project_id__knowledge_chunks_preview_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeChunkPreview"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    citation_get_preview: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                citation_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CitationPreview"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Citation stale */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Invalid citation ID */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge runtime unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_list_documents: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
            };
            header?: never;
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DocumentPage"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Invalid list request */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge runtime unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_upload_document: {
        parameters: {
            query?: never;
            header: {
                "idempotency-key": string;
            };
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "multipart/form-data": components["schemas"]["Body_knowledge_upload_document"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DocumentAccepted"];
                };
            };
            /** @description Unsupported document */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Document too large */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Invalid document upload */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Document limit reached */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge runtime unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_get_document: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                document_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DocumentDetail"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Document not found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Invalid document ID */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge runtime unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_delete_document: {
        parameters: {
            query?: never;
            header: {
                "idempotency-key": string;
            };
            path: {
                document_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Document not found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Document state changed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Invalid document ID */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge runtime unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    get_settings_api_v1_projects__project_id__knowledge_documents__document_id__chunk_settings_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                document_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeChunkSettingsView"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    save_settings_api_v1_projects__project_id__knowledge_documents__document_id__chunk_settings_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                document_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["KnowledgeChunkSettingsSave"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeChunkSettingsView"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_chunks_api_v1_projects__project_id__knowledge_documents__document_id__chunks_get: {
        parameters: {
            query?: {
                q?: string;
                status?: "all" | "enabled" | "disabled";
                page?: number;
                pageSize?: number;
            };
            header?: never;
            path: {
                document_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeChunkPage"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_chunk_api_v1_projects__project_id__knowledge_documents__document_id__chunks_post: {
        parameters: {
            query?: never;
            header?: {
                "Idempotency-Key"?: string | null;
            };
            path: {
                document_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["KnowledgeChunkCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeChunk"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    batch_api_v1_projects__project_id__knowledge_documents__document_id__chunks_batch_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                document_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["KnowledgeChunkBatch"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeChunkBatchResult"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    import_chunks_api_v1_projects__project_id__knowledge_documents__document_id__chunks_import_post: {
        parameters: {
            query?: never;
            header?: {
                "Idempotency-Key"?: string | null;
            };
            path: {
                document_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["KnowledgeChunkImport"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeChunkBatchResult"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    preview_api_v1_projects__project_id__knowledge_documents__document_id__chunks_preview_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                document_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["KnowledgeChunkSettingsRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeChunkPreview"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_chunk_api_v1_projects__project_id__knowledge_documents__document_id__chunks__chunk_id__delete: {
        parameters: {
            query: {
                version: number;
            };
            header?: never;
            path: {
                document_id: string;
                chunk_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    change_chunk_api_v1_projects__project_id__knowledge_documents__document_id__chunks__chunk_id__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                document_id: string;
                chunk_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["KnowledgeChunkChange"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeChunk"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_child_api_v1_projects__project_id__knowledge_documents__document_id__chunks__chunk_id__children_post: {
        parameters: {
            query?: never;
            header?: {
                "Idempotency-Key"?: string | null;
            };
            path: {
                document_id: string;
                chunk_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["KnowledgeChunkCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeChunk"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    original_api_v1_projects__project_id__knowledge_documents__document_id__original_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                document_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    knowledge_retry_document: {
        parameters: {
            query?: never;
            header: {
                "idempotency-key": string;
            };
            path: {
                document_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DocumentAccepted"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Document not found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Document is not retryable */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Invalid document ID */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge runtime unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_open_document_review: {
        parameters: {
            query?: never;
            header: {
                "idempotency-key": string;
            };
            path: {
                document_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["KnowledgeReviewOpenRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeReviewDetail"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    graph_get_evidence: {
        parameters: {
            query: {
                snapshotId: string;
            };
            header?: never;
            path: {
                evidence_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GraphEvidenceView"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    graph_get_node: {
        parameters: {
            query: {
                snapshotId: string;
            };
            header?: never;
            path: {
                node_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GraphSubgraphView"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    graph_get_neighbors: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                node_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GraphNeighborRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GraphSubgraphView"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    graph_bounded_path: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GraphPathRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GraphSubgraphView"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    graph_search: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GraphSearchRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GraphSubgraphView"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    graph_list_active_snapshots: {
        parameters: {
            query: {
                sourceRevisionId: string[];
            };
            header?: never;
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GraphSnapshotPage"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description Graph unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_get_current_publication: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgePublicationDetail"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_get_publication: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                publication_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgePublicationDetail"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_withdraw_publication: {
        parameters: {
            query?: never;
            header: {
                "if-match": string;
                "idempotency-key": string;
            };
            path: {
                publication_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgePublicationDetail"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_list_published_sources: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PublishedKnowledgeSourcePage"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_list_reviews: {
        parameters: {
            query?: {
                sourceRevisionId?: string | null;
                limit?: number;
                afterReviewId?: string | null;
            };
            header?: never;
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeReviewPage"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_get_review: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                review_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeReviewDetail"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_approve_review: {
        parameters: {
            query?: never;
            header: {
                "if-match": string;
            };
            path: {
                review_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeReviewSummary"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_list_review_decision_history: {
        parameters: {
            query?: {
                limit?: number;
                afterVersion?: number | null;
            };
            header?: never;
            path: {
                review_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeReviewDecisionPage"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_get_review_flowchart: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                review_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeFlowchart"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_correct_review_flowchart: {
        parameters: {
            query?: never;
            header: {
                "if-match": string;
            };
            path: {
                review_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["KnowledgeFlowchart"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeFlowchartCorrection"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_list_review_history: {
        parameters: {
            query?: {
                limit?: number;
                afterVersion?: number | null;
            };
            header?: never;
            path: {
                review_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeReviewHistoryPage"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_list_review_inventory: {
        parameters: {
            query?: {
                limit?: number;
                afterItemId?: string | null;
            };
            header?: never;
            path: {
                review_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeReviewInventory"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_compare_review_item: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                review_id: string;
                item_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeReviewItemComparison"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_update_review_item_decision: {
        parameters: {
            query?: never;
            header: {
                "if-match": string;
            };
            path: {
                review_id: string;
                item_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["KnowledgeReviewDecisionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeReviewDetail"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_read_review_original: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                review_id: string;
                item_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/octet-stream": string;
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_read_review_original_image: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                review_id: string;
                item_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_list_review_publications: {
        parameters: {
            query?: {
                limit?: number;
                afterPublicationId?: string | null;
            };
            header?: never;
            path: {
                review_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgePublicationPage"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_publish_review: {
        parameters: {
            query?: never;
            header: {
                "if-match": string;
                "idempotency-key": string;
            };
            path: {
                review_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["KnowledgePublishRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgePublicationDetail"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_return_review: {
        parameters: {
            query?: never;
            header: {
                "if-match": string;
            };
            path: {
                review_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeReviewDetail"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_submit_review: {
        parameters: {
            query?: never;
            header: {
                "if-match": string;
            };
            path: {
                review_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeReviewDetail"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Knowledge review request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_list_sources: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
            };
            header?: never;
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SourcePage"];
                };
            };
            /** @description Source request failed */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_upload_source: {
        parameters: {
            query?: never;
            header: {
                "idempotency-key": string;
            };
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "multipart/form-data": components["schemas"]["Body_knowledge_upload_source"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SourceAccepted"];
                };
            };
            /** @description Source request failed */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_get_source: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
            };
            header?: never;
            path: {
                source_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SourceDetail"];
                };
            };
            /** @description Source request failed */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_delete_source: {
        parameters: {
            query?: never;
            header: {
                "idempotency-key": string;
            };
            path: {
                source_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Source request failed */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    knowledge_retry_source: {
        parameters: {
            query?: never;
            header: {
                "idempotency-key": string;
            };
            path: {
                source_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SourceRetryRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SourceAccepted"];
                };
            };
            /** @description Source request failed */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Source request failed */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    prompt_suggestion_list: {
        parameters: {
            query: {
                locale: "en" | "zh";
            };
            header?: never;
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PromptSuggestionPage"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    test_plan_list_revisions: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TestPlanRevisionPage"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    test_plan_request_generation: {
        parameters: {
            query?: never;
            header: {
                "idempotency-key": string;
            };
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TestPlanGenerationRequestBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TestPlanGenerationAccepted"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    test_plan_get_generation: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TestPlanGenerationAccepted"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    test_plan_cancel_generation: {
        parameters: {
            query?: never;
            header: {
                "If-Match": number;
                "idempotency-key": string;
            };
            path: {
                job_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TestPlanGenerationAccepted"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    test_plan_retry_generation: {
        parameters: {
            query?: never;
            header: {
                "If-Match": number;
                "idempotency-key": string;
            };
            path: {
                job_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TestPlanGenerationAccepted"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    test_plan_review_summary: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TestPlanReviewSummaryView"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    test_plan_get_revision: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                test_plan_id: string;
                revision_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TestPlanRevisionView"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    test_plan_replace_draft: {
        parameters: {
            query?: never;
            header: {
                "If-Match": number;
                "idempotency-key": string;
            };
            path: {
                test_plan_id: string;
                revision_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TestPlanRevisionUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TestPlanRevisionView"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    test_plan_get_evidence_preview: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                test_plan_id: string;
                revision_id: string;
                citation_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TestPlanEvidencePreview"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    test_plan_fork_revision: {
        parameters: {
            query?: never;
            header: {
                "If-Match": number;
                "idempotency-key": string;
            };
            path: {
                test_plan_id: string;
                revision_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TestPlanRevisionView"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    test_plan_publish_revision: {
        parameters: {
            query?: never;
            header: {
                "If-Match": number;
                "idempotency-key": string;
            };
            path: {
                test_plan_id: string;
                revision_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TestPlanRevisionView"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    test_plan_review_revision: {
        parameters: {
            query?: never;
            header: {
                "If-Match": number;
                "idempotency-key": string;
            };
            path: {
                test_plan_id: string;
                revision_id: string;
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TestPlanReviewRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TestPlanRevisionView"];
                };
            };
            /** @description Project scope or authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    runtime_get_mode: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RuntimeMode"];
                };
            };
            /** @description Authorization denied */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Runtime unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
    health_get_live: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LiveHealth"];
                };
            };
        };
    };
    health_get_ready: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReadyHealth"];
                };
            };
        };
    };
    chat_create_turn: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                chat_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ChatTurnRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ChatTurnAccepted"];
                };
            };
            /** @description Request validation failed */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
            /** @description Turn workflow not implemented */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": components["schemas"]["ProblemDetails"];
                };
            };
        };
    };
}
