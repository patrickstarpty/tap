export interface paths {
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
    "/v1/citations/{citation_id}": {
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
    "/v1/knowledge/answers": {
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
    "/v1/knowledge/documents": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Documents */
        get: operations["knowledge_list_documents"];
        put?: never;
        /** Upload Document */
        post: operations["knowledge_upload_document"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/knowledge/documents/{document_id}": {
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
    "/v1/knowledge/documents/{document_id}/retry": {
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
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /**
         * AbstentionReason
         * @enum {string}
         */
        AbstentionReason: "insufficient_evidence" | "conflicting_sources" | "revision_mismatch";
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
            scenarioId: string | null;
            /**
             * Stepid
             * @default null
             */
            stepId: string | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "bdd";
        };
        /** Body_knowledge_upload_document */
        Body_knowledge_upload_document: {
            /**
             * Upload
             * Format: binary
             */
            upload: string;
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
            answerMode: components["schemas"]["AnswerMode"];
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
            prefix: string;
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
            suffix: string;
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
            symbol: string | null;
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
            bbox: number[] | null;
            /**
             * Endoffset
             * @default null
             */
            endOffset: number | null;
            /**
             * Headingpath
             * @default null
             */
            headingPath: string[] | null;
            /**
             * Page
             * @default null
             */
            page: number | null;
            /**
             * Startoffset
             * @default null
             */
            startOffset: number | null;
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
            errorCode: string | null;
            /**
             * Errorsummary
             * @default null
             */
            errorSummary: string | null;
            /** Filename */
            filename: string;
            /**
             * Mediatype
             * @enum {string}
             */
            mediaType: "application/pdf" | "application/vnd.openxmlformats-officedocument.wordprocessingml.document" | "text/markdown" | "text/plain";
            /**
             * Normalizedpreview
             * @default null
             */
            normalizedPreview: string | null;
            /** Revisionid */
            revisionId: string;
            /** Sourcecontenthash */
            sourceContentHash: string;
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
            nextCursor: string | null;
        };
        /** DocumentStageSnapshot */
        DocumentStageSnapshot: {
            /**
             * Completedat
             * @default null
             */
            completedAt: string | null;
            /**
             * Errorcode
             * @default null
             */
            errorCode: string | null;
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
            errorCode: string | null;
            /**
             * Errorsummary
             * @default null
             */
            errorSummary: string | null;
            /** Filename */
            filename: string;
            /**
             * Mediatype
             * @enum {string}
             */
            mediaType: "application/pdf" | "application/vnd.openxmlformats-officedocument.wordprocessingml.document" | "text/markdown" | "text/plain";
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
            runId: string | null;
            /**
             * Timeend
             * @default null
             */
            timeEnd: string | null;
            /**
             * Timestart
             * @default null
             */
            timeStart: string | null;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "failure";
        };
        /** HealthComponent */
        HealthComponent: {
            name: components["schemas"]["HealthComponentName"];
            /** @default null */
            remediationCode: components["schemas"]["HealthRemediationCode"] | null;
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
        /** LiveHealth */
        LiveHealth: {
            /**
             * Status
             * @constant
             */
            status: "ok";
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
            anchor: components["schemas"]["StructuralAnchor"] | null;
            family: components["schemas"]["SourceFamily"];
            /** @default preferred */
            mode: components["schemas"]["ResourceMode"];
            /**
             * Requestedrevision
             * @default null
             */
            requestedRevision: string | null;
            /** Sourceid */
            sourceId: string;
        };
        /**
         * RetrievalAnswerRequest
         * @description Grounded-answer intent with the same narrowing-only search fields.
         */
        RetrievalAnswerRequest: {
            /** @default quick */
            answerMode: components["schemas"]["AnswerMode"];
            /** Query */
            query: string;
            /**
             * Requestedcorpusversion
             * @default null
             */
            requestedCorpusVersion: string | null;
            /**
             * Requestedenvironment
             * @default null
             */
            requestedEnvironment: string | null;
            /**
             * Resourcerefs
             * @default null
             */
            resourceRefs: components["schemas"]["ResourceRef"][] | null;
            /**
             * Sources
             * @default null
             */
            sources: components["schemas"]["SourceFamily"][] | null;
            /**
             * Topk
             * @default null
             */
            topK: number | null;
        };
        /** RetrievalAnswerResponse */
        RetrievalAnswerResponse: {
            /** Abstained */
            abstained: boolean;
            /** @default null */
            abstentionReason: components["schemas"]["AbstentionReason"] | null;
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
            degradationReasons: string[] | null;
            /** Degradedmode */
            degradedMode: boolean;
            /** Queryplanid */
            queryPlanId: string;
            /** Retrievalprofileid */
            retrievalProfileId: string;
            /** Traceid */
            traceId: string;
        };
        /** RetrievalCitation */
        RetrievalCitation: {
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
            derivedFromChunkIds: string[] | null;
            /** Evidencelabel */
            evidenceLabel: string;
            /** Logicalchunkid */
            logicalChunkId: string;
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
            /** Schemaversion */
            schemaVersion: string;
            scores: components["schemas"]["RetrievalScores"];
            source: components["schemas"]["RetrievalSourceRevision"];
            /**
             * Title
             * @default null
             */
            title: string | null;
        };
        /** RetrievalScores */
        RetrievalScores: {
            /**
             * Bm25
             * @default null
             */
            bm25: number | null;
            /**
             * Exact
             * @default null
             */
            exact: number | null;
            /**
             * Rerank
             * @default null
             */
            rerank: number | null;
            /**
             * Rrf
             * @default null
             */
            rrf: number | null;
            /**
             * Vector
             * @default null
             */
            vector: number | null;
        };
        /**
         * RetrievalSearchRequest
         * @description Browser-visible retrieval intent; all authoritative scope is omitted.
         */
        RetrievalSearchRequest: {
            /** @default quick */
            answerMode: components["schemas"]["AnswerMode"];
            /** Query */
            query: string;
            /**
             * Requestedcorpusversion
             * @default null
             */
            requestedCorpusVersion: string | null;
            /**
             * Requestedenvironment
             * @default null
             */
            requestedEnvironment: string | null;
            /**
             * Resourcerefs
             * @default null
             */
            resourceRefs: components["schemas"]["ResourceRef"][] | null;
            /**
             * Sources
             * @default null
             */
            sources: components["schemas"]["SourceFamily"][] | null;
            /**
             * Topk
             * @default null
             */
            topK: number | null;
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
            degradationReasons: string[] | null;
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
         * SourceFamily
         * @enum {string}
         */
        SourceFamily: "doc" | "code" | "bdd" | "failure";
        /**
         * StructuralAnchor
         * @description A closed, structural location inside one authorized source family.
         */
        StructuralAnchor: components["schemas"]["DocumentAnchor"] | components["schemas"]["CodeAnchor"] | components["schemas"]["BddAnchor"] | components["schemas"]["OpenApiAnchor"] | components["schemas"]["FailureAnchor"];
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
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
                    "application/problem+json": {
                        /** Detail */
                        detail: string;
                        /** Instance */
                        instance?: string | null;
                        /** Status */
                        status: number;
                        /** Title */
                        title: string;
                        /** Type */
                        type: string;
                    };
                };
            };
            /** @description Turn workflow not implemented */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": {
                        /** Detail */
                        detail: string;
                        /** Instance */
                        instance?: string | null;
                        /** Status */
                        status: number;
                        /** Title */
                        title: string;
                        /** Type */
                        type: string;
                    };
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
            /** @description Invalid citation ID */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": {
                        /** Detail */
                        detail: string;
                        /** Instance */
                        instance?: string | null;
                        /** Status */
                        status: number;
                        /** Title */
                        title: string;
                        /** Type */
                        type: string;
                    };
                };
            };
            /** @description Knowledge runtime unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": {
                        /** Detail */
                        detail: string;
                        /** Instance */
                        instance?: string | null;
                        /** Status */
                        status: number;
                        /** Title */
                        title: string;
                        /** Type */
                        type: string;
                    };
                };
            };
        };
    };
    knowledge_create_answer: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
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
                    "application/json": components["schemas"]["RetrievalAnswerResponse"];
                };
            };
            /** @description Invalid answer request */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": {
                        /** Detail */
                        detail: string;
                        /** Instance */
                        instance?: string | null;
                        /** Status */
                        status: number;
                        /** Title */
                        title: string;
                        /** Type */
                        type: string;
                    };
                };
            };
            /** @description Knowledge runtime unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": {
                        /** Detail */
                        detail: string;
                        /** Instance */
                        instance?: string | null;
                        /** Status */
                        status: number;
                        /** Title */
                        title: string;
                        /** Type */
                        type: string;
                    };
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
                    "application/json": components["schemas"]["DocumentPage"];
                };
            };
            /** @description Invalid list request */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": {
                        /** Detail */
                        detail: string;
                        /** Instance */
                        instance?: string | null;
                        /** Status */
                        status: number;
                        /** Title */
                        title: string;
                        /** Type */
                        type: string;
                    };
                };
            };
            /** @description Knowledge runtime unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": {
                        /** Detail */
                        detail: string;
                        /** Instance */
                        instance?: string | null;
                        /** Status */
                        status: number;
                        /** Title */
                        title: string;
                        /** Type */
                        type: string;
                    };
                };
            };
        };
    };
    knowledge_upload_document: {
        parameters: {
            query?: never;
            header?: {
                "content-length"?: number | null;
            };
            path?: never;
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
            /** @description Document too large */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": {
                        /** Detail */
                        detail: string;
                        /** Instance */
                        instance?: string | null;
                        /** Status */
                        status: number;
                        /** Title */
                        title: string;
                        /** Type */
                        type: string;
                    };
                };
            };
            /** @description Invalid document upload */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": {
                        /** Detail */
                        detail: string;
                        /** Instance */
                        instance?: string | null;
                        /** Status */
                        status: number;
                        /** Title */
                        title: string;
                        /** Type */
                        type: string;
                    };
                };
            };
            /** @description Knowledge runtime unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": {
                        /** Detail */
                        detail: string;
                        /** Instance */
                        instance?: string | null;
                        /** Status */
                        status: number;
                        /** Title */
                        title: string;
                        /** Type */
                        type: string;
                    };
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
            /** @description Invalid document ID */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": {
                        /** Detail */
                        detail: string;
                        /** Instance */
                        instance?: string | null;
                        /** Status */
                        status: number;
                        /** Title */
                        title: string;
                        /** Type */
                        type: string;
                    };
                };
            };
            /** @description Knowledge runtime unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": {
                        /** Detail */
                        detail: string;
                        /** Instance */
                        instance?: string | null;
                        /** Status */
                        status: number;
                        /** Title */
                        title: string;
                        /** Type */
                        type: string;
                    };
                };
            };
        };
    };
    knowledge_delete_document: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                document_id: string;
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
            /** @description Invalid document ID */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": {
                        /** Detail */
                        detail: string;
                        /** Instance */
                        instance?: string | null;
                        /** Status */
                        status: number;
                        /** Title */
                        title: string;
                        /** Type */
                        type: string;
                    };
                };
            };
            /** @description Knowledge runtime unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": {
                        /** Detail */
                        detail: string;
                        /** Instance */
                        instance?: string | null;
                        /** Status */
                        status: number;
                        /** Title */
                        title: string;
                        /** Type */
                        type: string;
                    };
                };
            };
        };
    };
    knowledge_retry_document: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                document_id: string;
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
            /** @description Invalid document ID */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": {
                        /** Detail */
                        detail: string;
                        /** Instance */
                        instance?: string | null;
                        /** Status */
                        status: number;
                        /** Title */
                        title: string;
                        /** Type */
                        type: string;
                    };
                };
            };
            /** @description Knowledge runtime unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/problem+json": {
                        /** Detail */
                        detail: string;
                        /** Instance */
                        instance?: string | null;
                        /** Status */
                        status: number;
                        /** Title */
                        title: string;
                        /** Type */
                        type: string;
                    };
                };
            };
        };
    };
}
