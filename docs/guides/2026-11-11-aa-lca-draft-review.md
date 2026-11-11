# Review an AA result as an LCA draft

A passed live Agentic Automation task can be opened using **Review as LCA draft** in the task result, or by entering its task ID in the TAP LCA workspace's **Import an Agentic Automation result** section. Preview is read-only. **Use as unsaved draft** loads the projected actions into the existing BDD editor; saving, running and publishing remain separate explicit actions.

The gateway obtains the project-scoped task and its original execution, bundle, input digest and BDD action mapping. The browser service checks those bindings against its own stored execution/session/bundle, checks completion result and evidence, and requires a contiguous source action history. It projects actual successful action receipts, never generated script text or a caller-provided action list. Original source IDs and BDD expectations remain in step provenance; original scripts and evidence stay in the AA task.

## Projection rules

- Preserve source execution order, including repeated nonconsecutive BDD groups.
- Navigation, clicks, fill and native select can become editable steps. A server-confirmed controlled-action receipt supplies the semantic target and safe acknowledged input; input vault references are never decrypted by this feature.
- Uncontrolled native-select labels require observed options and the resulting canonical option value. Custom widgets without that evidence require review rather than being silently converted to native selects.
- Read assertions retain their attribute, output name and all original constraints through `assert_read`. Unsupported attributes or patterns cannot be weakened into existence checks.
- CSS and server-observed replacements for ephemeral references require target review. Ambiguous references, protected inputs, failed/unknown actions, human interventions, unsupported actions and missing business-step mappings produce explicit issues.
- Any issue prevents creation of an executable definition. The user can still inspect the issue report and source task; no partial executable flow silently omits an operation.

## Acceptance boundary

Unit and UI-component tests establish source authorization, loss-aware projection and explicit import behavior. A passed original AA task does not certify a newly edited LCA flow. Product acceptance requires loading a real completed task, reviewing the imported BDD/action tree, saving it, running its generated script, checking assertions and named outputs, and reviewing the run evidence. A missing real successful source task must not be replaced by a claim of end-to-end acceptance.
