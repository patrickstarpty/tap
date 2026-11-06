"""Keep pre-refactor checkpoint characterization fixtures exercising the new executor.

Production graph callers use the typed context and values directly. These helpers
let historical JSON fixtures retain their original assertions at the codec seam.
"""

from tap.interfaces.http.knowledge_service import KnowledgeHttpService as HttpService
from tap.modules.knowledge.api import (
    ConversationAnswerExecutor,
    ConversationExecutionContext,
    answer_request_from_http,
    conversation_answer_to_http,
    decode_conversation_step,
    decode_direct_answer,
    encode_conversation_step,
)


class KnowledgeHttpService(HttpService):
    _plan_deadline = staticmethod(ConversationAnswerExecutor._plan_deadline)

    def __getattr__(self, name):
        executor = self.conversation_answers
        if name == "_redact_final_response":

            async def redact(response):
                value = decode_direct_answer(response.model_dump(mode="json", by_alias=True))
                return conversation_answer_to_http(await executor._redact_answer_response(value))

            return redact
        method = getattr(executor, name)
        if name == "refuse_out_of_scope":

            async def refuse(*args, **kwargs):
                return conversation_answer_to_http(await method(*args, **kwargs))

            return refuse
        if name not in {
            "prepare_conversation_answer",
            "generate_conversation_answer",
            "verify_conversation_answer",
            "finalize_conversation_answer",
            "run_conversation_tool",
            "add_insights_records",
        }:
            return method

        async def historical(first, frozen=None, *args, answer_plan=None, authorize=None, **kwargs):
            if isinstance(frozen, ConversationExecutionContext):
                return await method(first, frozen, *args, **kwargs)
            if name == "add_insights_records":
                # Production graph passes a decoded step; historical JSON callers
                # still need the codec. Decode only the latter.
                encoded = isinstance(first.get("bundle"), dict)
                step, labels = await method(
                    decode_conversation_step(first) if encoded else first, frozen
                )
                return (encode_conversation_step(step) if encoded else step), labels
            context = ConversationExecutionContext(frozen, answer_plan, authorize)
            value = (
                answer_request_from_http(first)
                if name == "prepare_conversation_answer"
                else decode_conversation_step(first)
            )
            result = await method(value, context, *args, **kwargs)
            if name == "finalize_conversation_answer":
                return conversation_answer_to_http(result)
            if name == "run_conversation_tool":
                return {**result, "step": encode_conversation_step(result["step"])}
            return encode_conversation_step(result)

        return historical
