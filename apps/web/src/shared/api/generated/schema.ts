export interface paths {
    "/api/v1/projects/{project_id}/insights/evidence/{receipt_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Download Evidence */
        get: operations["download_evidence_api_v1_projects__project_id__insights_evidence__receipt_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/insights/failures": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Failures */
        get: operations["list_failures_api_v1_projects__project_id__insights_failures_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/insights/metrics": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Metrics */
        get: operations["list_metrics_api_v1_projects__project_id__insights_metrics_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/insights/queries": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Query */
        post: operations["create_query_api_v1_projects__project_id__insights_queries_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/insights/queries/{query_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Query */
        get: operations["get_query_api_v1_projects__project_id__insights_queries__query_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/insights/reports": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Receive Report */
        post: operations["receive_report_api_v1_projects__project_id__insights_reports_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/insights/reports/{receipt_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Report */
        get: operations["get_report_api_v1_projects__project_id__insights_reports__receipt_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/insights/reports/{receipt_id}/retry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Retry Report */
        post: operations["retry_report_api_v1_projects__project_id__insights_reports__receipt_id__retry_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/insights/runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Runs */
        get: operations["list_runs_api_v1_projects__project_id__insights_runs_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/insights/runs/{run_id}/attempts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Run Attempts */
        get: operations["list_run_attempts_api_v1_projects__project_id__insights_runs__run_id__attempts_get"];
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
        /** Live */
        get: operations["live_health_live_get"];
        put?: never;
        post?: never;
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
        /** AttemptDetailContract */
        AttemptDetailContract: {
            /** Attempt */
            attempt: number | null;
            /** Datarow */
            dataRow: string | null;
            /** Durationseconds */
            durationSeconds: number | null;
            /** Evidencerefs */
            evidenceRefs: string[];
            /** Externalrunid */
            externalRunId: string;
            /** Factkey */
            factKey: string;
            /** Result */
            result: string;
            /** Sourcetestidentity */
            sourceTestIdentity: string;
            /** Stabletestid */
            stableTestId: string | null;
        };
        /** AttemptPageContract */
        AttemptPageContract: {
            /** Items */
            items: components["schemas"]["AttemptDetailContract"][];
            /** Nextcursor */
            nextCursor: string | null;
            /** Queryid */
            queryId: string;
            /** Runid */
            runId: string;
        };
        /** FactWatermarkContract */
        FactWatermarkContract: {
            /** Projectionversion */
            projectionVersion: string;
            /** Visibledataversion */
            visibleDataVersion: number;
        };
        /** FailureDetailContract */
        FailureDetailContract: {
            /** Configuration */
            configuration: string;
            /** Datarow */
            dataRow: string | null;
            /** Evidencerefs */
            evidenceRefs: string[];
            /** Externalrunid */
            externalRunId: string;
            /** Factkey */
            factKey: string;
            /**
             * Result
             * @enum {string}
             */
            result: "fail" | "error";
            /** Runid */
            runId: string;
            /** Sourceid */
            sourceId: string;
            /** Sourcetestidentity */
            sourceTestIdentity: string;
            /** Stabletestid */
            stableTestId: string | null;
        };
        /** FailurePageContract */
        FailurePageContract: {
            /** Items */
            items: components["schemas"]["FailureDetailContract"][];
            /** Nextcursor */
            nextCursor: string | null;
            /** Queryid */
            queryId: string;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** MetricCatalogContract */
        MetricCatalogContract: {
            /** Items */
            items: components["schemas"]["MetricCatalogItemContract"][];
        };
        /** MetricCatalogItemContract */
        MetricCatalogItemContract: {
            /** Definition */
            definition: string;
            /** Label */
            label: string;
            metricId: components["schemas"]["MetricId"];
            /** Metricversion */
            metricVersion: string;
            /**
             * Unit
             * @enum {string}
             */
            unit: "ratio" | "count" | "seconds";
        };
        /** MetricFiltersContract */
        MetricFiltersContract: {
            /**
             * Branches
             * @default []
             */
            branches?: string[];
            /**
             * Buildids
             * @default []
             */
            buildIds?: string[];
            /**
             * Configurations
             * @default []
             */
            configurations?: string[];
            /**
             * Environments
             * @default []
             */
            environments?: string[];
            /**
             * Runids
             * @default []
             */
            runIds?: string[];
            /**
             * Sourceids
             * @default []
             */
            sourceIds?: string[];
        };
        /**
         * MetricId
         * @enum {string}
         */
        MetricId: "first_pass_rate" | "final_pass_rate" | "retry_recovery_rate" | "recovery_contribution_rate" | "skipped_count" | "p95_duration_seconds";
        /** MetricQueryRequest */
        MetricQueryRequest: {
            /**
             * Asof
             * Format: date-time
             */
            asOf: string;
            filters?: components["schemas"]["MetricFiltersContract"];
            /** From */
            from: string;
            /** Metricids */
            metricIds: components["schemas"]["MetricId"][];
            /** Timezone */
            timezone: string;
            /** To */
            to: string;
        };
        /** MetricQueryResponse */
        MetricQueryResponse: {
            /**
             * Asof
             * Format: date-time
             */
            asOf: string;
            /**
             * Createdat
             * Format: date-time
             */
            createdAt: string;
            factWatermark: components["schemas"]["FactWatermarkContract"];
            filters: components["schemas"]["MetricFiltersContract"];
            /** From */
            from: string;
            /** Metricversion */
            metricVersion: string;
            /** Metrics */
            metrics: components["schemas"]["MetricResultContract"][];
            /** Queryid */
            queryId: string;
            /** Timezone */
            timezone: string;
            /** To */
            to: string;
            /** Trends */
            trends: components["schemas"]["TrendPointContract"][];
        };
        /** MetricResultContract */
        MetricResultContract: {
            /**
             * Completeness
             * @enum {string}
             */
            completeness: "complete" | "empty" | "unavailable";
            /** Denominator */
            denominator: number | null;
            /** Evidencerefs */
            evidenceRefs: string[];
            metricId: components["schemas"]["MetricId"];
            /** Missingreasons */
            missingReasons: string[];
            /** Numerator */
            numerator: number | null;
            /** Value */
            value: number | null;
        };
        /** RunPageContract */
        RunPageContract: {
            /** Items */
            items: components["schemas"]["RunSummaryContract"][];
            /** Nextcursor */
            nextCursor: string | null;
            /** Queryid */
            queryId: string;
        };
        /** RunSummaryContract */
        RunSummaryContract: {
            /** Branch */
            branch: string | null;
            /** Buildid */
            buildId: string | null;
            /** Configuration */
            configuration: string;
            /** Environment */
            environment: string;
            /** Evidencerefs */
            evidenceRefs: string[];
            /** Externalrunid */
            externalRunId: string;
            /** Instancecount */
            instanceCount: number;
            /** Runid */
            runId: string;
            /** Sourceid */
            sourceId: string;
            /** Startedat */
            startedAt: string | null;
        };
        /** TrendPointContract */
        TrendPointContract: {
            /** Localdate */
            localDate: string;
            /** Metrics */
            metrics: components["schemas"]["MetricResultContract"][];
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
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    download_evidence_api_v1_projects__project_id__insights_evidence__receipt_id__get: {
        parameters: {
            query?: never;
            header?: {
                Authorization?: string | null;
            };
            path: {
                project_id: string;
                receipt_id: string;
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
    list_failures_api_v1_projects__project_id__insights_failures_get: {
        parameters: {
            query: {
                queryId: string;
                cursor?: number;
                limit?: number;
            };
            header?: {
                Authorization?: string | null;
            };
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
                    "application/json": components["schemas"]["FailurePageContract"];
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
    list_metrics_api_v1_projects__project_id__insights_metrics_get: {
        parameters: {
            query?: never;
            header?: {
                Authorization?: string | null;
            };
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
                    "application/json": components["schemas"]["MetricCatalogContract"];
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
    create_query_api_v1_projects__project_id__insights_queries_post: {
        parameters: {
            query?: never;
            header?: {
                Authorization?: string | null;
            };
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MetricQueryRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MetricQueryResponse"];
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
    get_query_api_v1_projects__project_id__insights_queries__query_id__get: {
        parameters: {
            query?: never;
            header?: {
                Authorization?: string | null;
            };
            path: {
                project_id: string;
                query_id: string;
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
                    "application/json": components["schemas"]["MetricQueryResponse"];
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
    receive_report_api_v1_projects__project_id__insights_reports_post: {
        parameters: {
            query?: never;
            header: {
                "X-TAP-Report-Manifest": string;
                Authorization?: string | null;
            };
            path: {
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
                    "application/json": {
                        [key: string]: unknown;
                    };
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
    get_report_api_v1_projects__project_id__insights_reports__receipt_id__get: {
        parameters: {
            query?: never;
            header?: {
                Authorization?: string | null;
            };
            path: {
                project_id: string;
                receipt_id: string;
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
                    "application/json": {
                        [key: string]: unknown;
                    };
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
    retry_report_api_v1_projects__project_id__insights_reports__receipt_id__retry_post: {
        parameters: {
            query?: never;
            header?: {
                Authorization?: string | null;
            };
            path: {
                project_id: string;
                receipt_id: string;
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
                    "application/json": {
                        [key: string]: unknown;
                    };
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
    list_runs_api_v1_projects__project_id__insights_runs_get: {
        parameters: {
            query: {
                queryId: string;
                cursor?: number;
                limit?: number;
            };
            header?: {
                Authorization?: string | null;
            };
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
                    "application/json": components["schemas"]["RunPageContract"];
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
    list_run_attempts_api_v1_projects__project_id__insights_runs__run_id__attempts_get: {
        parameters: {
            query: {
                queryId: string;
                cursor?: number;
                limit?: number;
            };
            header?: {
                Authorization?: string | null;
            };
            path: {
                project_id: string;
                run_id: string;
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
                    "application/json": components["schemas"]["AttemptPageContract"];
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
    live_health_live_get: {
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
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
        };
    };
}
