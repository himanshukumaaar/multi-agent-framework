import asyncio
import os
import re
from typing import AsyncGenerator, List
from uuid import uuid4

import streamlit as st
from dotenv import load_dotenv
from streamlit.runtime.scriptrunner import get_script_run_ctx
from client import AgentClient
from schema import ChatMessage, model_dump_compat, model_validate_compat


# A Streamlit app for interacting with the langgraph agent via a simple chat interface.
# The app has three main functions which are all run async:

# - main() - sets up the streamlit app and high level structure
# - draw_messages() - draws a set of chat messages - either replaying existing messages
#   or streaming new ones.
# - handle_feedback() - Draws a feedback widget and records feedback from the user.

# The app heavily uses AgentClient to interact with the agent's FastAPI endpoints.


APP_TITLE = "Research Assistant"
APP_ICON = "🔎"
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(ROOT_DIR, ".env"))
USER_AUTH_ENABLED = os.getenv("ENABLE_USER_AUTH", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}


def _extract_hitl_preview_context(text: str) -> dict | None:
    content = (text or "").strip()
    if not content:
        return None
    if "Human approval required before web-answer generation." not in content:
        return None

    # Support both single-line and multi-line render variants.
    query_match = re.search(
        r"Query:\s*(.+?)(?:\s+Recency target:\s*last\s*(\d+)\s+days|$)",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not query_match:
        return None

    query = (query_match.group(1) or "").strip()
    if not query:
        return None

    days_raw = query_match.group(2) or ""
    recency_days = int(days_raw) if days_raw.isdigit() else 0
    return {"query": query, "recency_days": recency_days}


def _history_rows_to_chat_messages(rows: list[dict]) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    for row in reversed(rows or []):
        role = str(row.get("role") or "").strip()
        if role not in {"human", "ai", "tool"}:
            continue
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        messages.append(
            ChatMessage(
                type=role,  # type: ignore[arg-type]
                content=content,
                run_id=str(row.get("run_id") or "") or None,
            )
        )
    return messages


def _thread_label(thread: dict) -> str:
    thread_id = str(thread.get("thread_id") or "")
    count = int(thread.get("message_count") or 0)
    when = str(thread.get("last_message_at") or "")
    preview = str(thread.get("last_message_preview") or "").replace("\n", " ").strip()
    if len(preview) > 64:
        preview = preview[:61] + "..."
    when_short = when.replace("T", " ")[:19] if when else "-"
    if preview:
        return f"{when_short} | {count} msgs | {preview}"
    return f"{when_short} | {count} msgs | {thread_id}"


@st.cache_resource
def get_agent_client():
    agent_url = os.getenv("AGENT_URL") or os.getenv("API_BASE_URL", "http://localhost:8000")
    return AgentClient(agent_url)


def get_available_models() -> dict[str, str]:
    models: dict[str, str] = {}
    if os.getenv("OPENAI_API_KEY"):
        models["OpenAI GPT-4o-mini (streaming)"] = "gpt-4o-mini"
    if os.getenv("GROQ_API_KEY"):
        models["llama-3.1-70b on Groq"] = "llama-3.1-70b"
    return models


async def main():
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon=APP_ICON,
        menu_items={},
    )

    # Hide the streamlit upper-right chrome
    st.html(
        """
        <style>
        [data-testid="stStatusWidget"] {
                visibility: hidden;
                height: 0%;
                position: fixed;
            }
        </style>
        """,
    )
    if st.get_option("client.toolbarMode") != "minimal":
        st.set_option("client.toolbarMode", "minimal")
        await asyncio.sleep(0.1)
        st.rerun()

    models = get_available_models()
    if not models:
        st.error(
            "No model API key found. Add OPENAI_API_KEY or GROQ_API_KEY to .env and restart Streamlit."
        )
        st.code("OPENAI_API_KEY=your_key_here\nGROQ_API_KEY=your_key_here")
        st.stop()

    agent_client = get_agent_client()
    if USER_AUTH_ENABLED:
        if "auth_user_id" not in st.session_state:
            st.session_state.auth_user_id = ""
        if "auth_token" not in st.session_state:
            st.session_state.auth_token = ""
        if "thread_summaries" not in st.session_state:
            st.session_state.thread_summaries = []
        if st.session_state.auth_token:
            agent_client.set_access_token(st.session_state.auth_token)

    ctx = get_script_run_ctx()
    if "thread_id" not in st.session_state:
        st.session_state.thread_id = (getattr(ctx, "session_id", None) if ctx else None) or str(uuid4())
    thread_id = st.session_state.thread_id

    # Config options
    with st.sidebar:
        st.header(f"{APP_ICON} {APP_TITLE}")
        if USER_AUTH_ENABLED:
            st.subheader("Account")
            if st.session_state.auth_user_id:
                st.success(f"Signed in: `{st.session_state.auth_user_id}`")
                col_new, col_refresh = st.columns(2)
                if col_new.button("New Chat"):
                    st.session_state.messages = []
                    st.session_state.thread_id = str(uuid4())
                    st.rerun()
                if col_refresh.button("Refresh"):
                    try:
                        threads_payload = await agent_client.alist_threads(limit=30)
                        st.session_state.thread_summaries = threads_payload.get("threads", [])
                        st.toast("Conversation list refreshed.")
                        st.rerun()
                    except Exception as e:
                        st.warning(f"Could not refresh history: {e}")

                thread_summaries = st.session_state.get("thread_summaries", [])
                thread_ids = [str(t.get("thread_id") or "") for t in thread_summaries if t.get("thread_id")]
                if thread_ids:
                    if st.session_state.thread_id not in thread_ids:
                        st.session_state.thread_id = thread_ids[0]
                    label_map = {
                        str(t.get("thread_id") or ""): _thread_label(t) for t in thread_summaries
                    }
                    selected_thread = st.selectbox(
                        "Conversation History",
                        options=thread_ids,
                        index=thread_ids.index(st.session_state.thread_id),
                        format_func=lambda tid: label_map.get(tid, tid),
                    )
                    if selected_thread != st.session_state.thread_id:
                        try:
                            history_payload = await agent_client.aget_store(selected_thread, limit=200)
                            st.session_state.thread_id = selected_thread
                            st.session_state.messages = _history_rows_to_chat_messages(
                                history_payload.get("messages", [])
                            )
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to load selected conversation: {e}")

                if st.button("Logout"):
                    st.session_state.auth_user_id = ""
                    st.session_state.auth_token = ""
                    st.session_state.messages = []
                    st.session_state.thread_summaries = []
                    if "thread_id" in st.session_state:
                        del st.session_state.thread_id
                    agent_client.set_access_token(None)
                    st.rerun()
            else:
                auth_mode = st.radio(
                    "Authentication",
                    options=["Login", "Register"],
                    horizontal=True,
                )
                auth_user_id = st.text_input("User ID", key="auth_user_id_input")
                auth_password = st.text_input("Password", type="password", key="auth_password_input")
                if st.button("Continue"):
                    try:
                        if auth_mode == "Register":
                            auth = await agent_client.aregister(auth_user_id, auth_password)
                        else:
                            auth = await agent_client.alogin(auth_user_id, auth_password)
                        st.session_state.auth_user_id = auth.user_id
                        st.session_state.auth_token = auth.access_token
                        agent_client.set_access_token(auth.access_token)

                        threads_payload = await agent_client.alist_threads(limit=30)
                        thread_summaries = threads_payload.get("threads", [])
                        st.session_state.thread_summaries = thread_summaries
                        if thread_summaries:
                            latest_thread_id = str(thread_summaries[0].get("thread_id") or "")
                            if latest_thread_id:
                                history_payload = await agent_client.aget_store(latest_thread_id, limit=200)
                                st.session_state.thread_id = latest_thread_id
                                st.session_state.messages = _history_rows_to_chat_messages(
                                    history_payload.get("messages", [])
                                )
                                st.success("Authentication successful. Loaded latest conversation history.")
                            else:
                                st.session_state.messages = []
                                st.session_state.thread_id = str(uuid4())
                                st.success("Authentication successful. Started a new conversation.")
                        else:
                            st.session_state.messages = []
                            st.session_state.thread_id = str(uuid4())
                            st.success("Authentication successful. Started a new conversation.")
                        await asyncio.sleep(0.1)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Authentication failed: {e}")

            if not st.session_state.auth_user_id:
                st.info("Login or register to start chatting.")

        st.caption("Navigation")
        view_mode = st.radio("Select View", options=["💬 Chat Assistant", "📊 AI Observability Dashboard"], index=0)

        st.caption("Thread ID (for /store/{thread_id})")
        st.code(thread_id)
        with st.popover(":material/settings: Settings"):
            m = st.radio("LLM to use", options=list(models.keys()))
            model = models[m]
            use_streaming = st.toggle("Stream results", value=True)
            st.caption("Web HITL runs inside backend graph (reply `approve` / `reject: reason`).")
        with st.popover(":material/policy: Privacy"):
            st.write("Prompts, responses and feedback in this app are anonymously recorded and saved to LangSmith for product evaluation and improvement purposes only.")

    if USER_AUTH_ENABLED and not st.session_state.auth_user_id:
        st.stop()

    if view_mode == "📊 AI Observability Dashboard":
        await _render_observability_dashboard()
        return

    thread_id = st.session_state.thread_id

    # Draw existing messages
    if "messages" not in st.session_state:
        st.session_state.messages = []
    messages: List[ChatMessage] = st.session_state.messages

    if len(messages) == 0:
        WELCOME = "Hello! I'm an AI assistant with web search, local RAG retrieval, and a calculator. Ask me anything!"
        with st.chat_message("ai"):
            st.write(WELCOME)

    # draw_messages() expects an async iterator over messages
    async def amessage_iter():
        for m in messages:
            yield m

    await draw_messages(amessage_iter())

    async def _submit_user_query(input_text: str, display_text: str | None = None) -> None:
        shown = (display_text or input_text or "").strip()
        messages.append(ChatMessage(type="human", content=shown))
        st.chat_message("human").write(shown)
        try:
            if use_streaming:
                stream = agent_client.astream(
                    message=input_text,
                    model=model,
                    thread_id=thread_id,
                )
                await draw_messages(stream, is_new=True)
            else:
                response = await agent_client.ainvoke(
                    message=input_text,
                    model=model,
                    thread_id=thread_id,
                )
                messages.append(response)
                st.chat_message("ai").write(response.content)

            # Keep local thread summary fresh after successful send.
            if USER_AUTH_ENABLED and st.session_state.get("auth_user_id"):
                try:
                    threads_payload = await agent_client.alist_threads(limit=30)
                    st.session_state.thread_summaries = threads_payload.get("threads", [])
                except Exception:
                    pass
        except Exception as e:
            err = f"Request failed: {e}"
            st.chat_message("ai").error(err)
            messages.append(ChatMessage(type="ai", content=err))

    pending_hitl = None
    if messages:
        latest = messages[-1]
        if latest.type == "ai":
            pending_hitl = _extract_hitl_preview_context(latest.content or "")

    if pending_hitl:
        with st.container(border=True):
            st.caption("Web HITL decision")
            st.write(
                f"Preview is waiting for your decision for query: `{pending_hitl['query']}`"
            )
            reject_reason = st.text_input(
                "Reject reason (optional)",
                key="web_hitl_reject_reason_input",
                placeholder="Example: Please use only last 3 days and include Reuters.",
            )
            col_approve, col_reject = st.columns(2)
            approve_clicked = col_approve.button("Approve", key="web_hitl_approve_btn")
            reject_clicked = col_reject.button("Reject", key="web_hitl_reject_btn")

        if approve_clicked:
            await _submit_user_query("approve", display_text="approve")
            st.rerun()
        if reject_clicked:
            reason = (reject_reason or "").strip()
            reject_text = f"reject: {reason}" if reason else "reject"
            await _submit_user_query(reject_text, display_text="reject")
            st.rerun()

    input_text = st.chat_input()
    if input_text:
        await _submit_user_query(input_text)
        st.rerun()  # Clear stale containers

    # If messages have been generated, show feedback widget
    if len(messages) > 0 and st.session_state.last_message is not None and messages[-1].type == "ai":
        with st.session_state.last_message:
            await handle_feedback()


async def draw_messages(
        messages_agen: AsyncGenerator[ChatMessage | str, None],
        is_new=False,
    ):
    """
    Draws a set of chat messages - either replaying existing messages
    or streaming new ones.

    This function has additional logic to handle streaming tokens and tool calls.
    - Use a placeholder container to render streaming tokens as they arrive.
    - Use a status container to render tool calls. Track the tool inputs and outputs
      and update the status container accordingly.
    
    The function also needs to track the last message container in session state
    since later messages can draw to the same container. This is also used for
    drawing the feedback widget in the latest chat message.

    Args:
        messages_aiter: An async iterator over messages to draw.
        is_new: Whether the messages are new or not.
    """

    # Keep track of the last message container
    last_message_type = None
    st.session_state.last_message = None

    # Placeholder for intermediate streaming tokens
    streaming_content = ""
    streaming_placeholder = None

    # Iterate over the messages and draw them
    while msg := await anext(messages_agen, None):
        # str message represents an intermediate token being streamed
        if isinstance(msg, str):
            # If placeholder is empty, this is the first token of a new message
            # being streamed. We need to do setup.
            if not streaming_placeholder:
                if last_message_type != "ai":
                    last_message_type = "ai"
                    st.session_state.last_message = st.chat_message("ai")
                with st.session_state.last_message:
                    streaming_placeholder = st.empty()
            
            streaming_content += msg
            streaming_placeholder.write(streaming_content)
            continue
        if not isinstance(msg, ChatMessage):
            # Streamlit reloads and mixed module import paths can produce a
            # ChatMessage-like object from a different class identity.
            # Normalize to the local ChatMessage model instead of failing.
            try:
                if isinstance(msg, dict):
                    msg = model_validate_compat(ChatMessage, msg)
                else:
                    msg = model_validate_compat(ChatMessage, model_dump_compat(msg))
            except Exception:
                st.error(f"Unexpected message type: {type(msg)}")
                st.write(msg)
                st.stop()
        match msg.type:
            # A message from the user, the easiest case
            case "human":
                last_message_type = "human"
                st.chat_message("human").write(msg.content)

            # A message from the agent is the most complex case, since we need to
            # handle streaming tokens and tool calls.
            case "ai":
                # If we're rendering new messages, store the message in session state
                if is_new:
                    st.session_state.messages.append(msg)
                
                # If the last message type was not AI, create a new chat message
                if last_message_type != "ai":
                    last_message_type = "ai"
                    st.session_state.last_message = st.chat_message("ai")
                
                with st.session_state.last_message:
                    # If the message has content, write it out.
                    # Reset the streaming variables to prepare for the next message.
                    if msg.content:
                        if streaming_placeholder:
                            streaming_placeholder.write(msg.content)
                            streaming_content = ""
                            streaming_placeholder = None
                        else:
                            st.write(msg.content)

                    if msg.tool_calls:
                        # Create a status container for each tool call and store the
                        # status container by ID to ensure results are mapped to the
                        # correct status container.
                        call_results = {}
                        for tool_call in msg.tool_calls:
                            status = st.status(
                                    f"""Tool Call: {tool_call["name"]}""",
                                    state="running" if is_new else "complete",
                                )
                            call_results[tool_call["id"]] = status
                            status.write("Input:")
                            status.write(tool_call["args"])

                        # Expect one ToolMessage for each tool call.
                        for _ in range(len(call_results)):
                            tool_result: ChatMessage = await anext(messages_agen)
                            if not tool_result.type == "tool":
                                st.error(f"Unexpected ChatMessage type: {tool_result.type}")
                                st.write(tool_result)
                                st.stop()
                            
                            # Record the message if it's new, and update the correct
                            # status container with the result
                            if is_new:
                                st.session_state.messages.append(tool_result)
                            status = call_results[tool_result.tool_call_id]
                            status.write("Output:")
                            status.write(tool_result.content)
                            status.update(state="complete")

            # In case of an unexpected message type, log an error and stop
            case _: 
                st.error(f"Unexpected ChatMessage type: {msg.type}")
                st.write(msg)
                st.stop()


async def handle_feedback():
    """Draws a feedback widget and records feedback from the user."""

    # Keep track of last feedback sent to avoid sending duplicates
    if "last_feedback" not in st.session_state:
        st.session_state.last_feedback = (None, None)
    
    latest_run_id = st.session_state.messages[-1].run_id
    if not latest_run_id:
        return
    feedback = st.feedback("stars", key=latest_run_id)

    # If the feedback value or run ID has changed, send a new feedback record
    if feedback and (latest_run_id, feedback) != st.session_state.last_feedback:
        
        # Normalize the feedback value (an index) to a score between 0 and 1
        normalized_score = (feedback + 1) / 5.0

        agent_client = get_agent_client()
        if st.session_state.get("auth_token"):
            agent_client.set_access_token(st.session_state.auth_token)
        await agent_client.acreate_feedback(
            run_id=latest_run_id,
            key="human-feedback-stars",
            score=normalized_score,
            kwargs=dict(
                comment="In-line human feedback",
            ),
        )
        st.session_state.last_feedback = (latest_run_id, feedback)
        st.toast("Feedback recorded", icon=":material/reviews:")


async def _render_observability_dashboard():
    st.title("📊 Enterprise AI Observability & Evaluation Dashboard")
    st.caption("Real-time monitoring, trace visualizer, heuristic quality evaluation, and token cost analytics.")

    agent_client = get_agent_client()
    if st.session_state.get("auth_token"):
        agent_client.set_access_token(st.session_state.auth_token)

    try:
        metrics = await agent_client.aget_observability_metrics()
    except Exception as e:
        st.error(f"Could not load observability metrics: {e}")
        metrics = {}

    col1, col2, col3, col4, col5 = st.columns(5)
    total_reqs = metrics.get("total_requests", 0)
    succ_rate = metrics.get("success_rate", 1.0) * 100
    avg_lat = metrics.get("avg_latency_ms", 0.0)
    total_cost = metrics.get("estimated_cost_usd", 0.0)
    avg_eval = metrics.get("avg_evaluation_score", 0.0)

    col1.metric("Total Requests", f"{total_reqs:,}")
    col2.metric("Success Rate", f"{succ_rate:.1f}%")
    col3.metric("Avg Latency", f"{avg_lat:.0f} ms")
    col4.metric("Est. Cost", f"${total_cost:.4f}")
    col5.metric("Avg Quality Score", f"{avg_eval:.1f} / 100")

    st.divider()

    tab_traces, tab_agents, tab_rag_mcp, tab_llm, tab_evals = st.tabs([
        "🔍 Trace Explorer",
        "🤖 Agent Performance",
        "📚 RAG & MCP Tools",
        "💰 LLM & Cost Analytics",
        "🎯 Response Evaluations"
    ])

    with tab_traces:
        st.subheader("Trace Explorer & Visual Execution Waterfall")
        traces_payload = await agent_client.aget_observability_traces(limit=30)
        traces_list = traces_payload.get("traces", [])
        if not traces_list:
            st.info("No execution traces recorded yet. Submit queries in the chat assistant to generate trace telemetry!")
        else:
            trace_options = {t["trace_id"]: f"{t['trace_id'][:8]}... | Status: {t['status']} | Duration: {t['duration_ms']:.0f}ms | Created: {t['created_at'][:19]}" for t in traces_list}
            selected_id = st.selectbox("Select Execution Trace", options=list(trace_options.keys()), format_func=lambda x: trace_options.get(x, x))
            
            if selected_id:
                trace_detail = await agent_client.aget_observability_trace_detail(selected_id)
                if trace_detail:
                    st.markdown(f"### Trace ID: `{selected_id}`")
                    st.json(trace_detail.get("metadata", {}))

                    st.subheader("Execution Timeline Waterfall")
                    agent_execs = trace_detail.get("agent_executions", [])
                    if agent_execs:
                        st.markdown("**Agent Execution Chain:**")
                        for idx, ag in enumerate(agent_execs, start=1):
                            status_icon = "✅" if ag.get("success") else "❌"
                            with st.expander(f"{status_icon} Step {idx}: {ag.get('agent_name')} ({ag.get('duration_ms', 0):.1f} ms)"):
                                st.write(f"- **Start Time:** `{ag.get('start_time')}`")
                                st.write(f"- **End Time:** `{ag.get('end_time')}`")
                                if ag.get("route_selected"):
                                    st.write(f"- **Route Selected:** `{ag.get('route_selected')}`")
                                if ag.get("error_type"):
                                    st.write(f"- **Error:** `{ag.get('error_type')}`")
                                st.json({"input_metadata": ag.get("input_metadata", {}), "output_metadata": ag.get("output_metadata", {})})
                    
                    llm_calls = trace_detail.get("llm_calls", [])
                    if llm_calls:
                        st.markdown("**LLM Calls:**")
                        for lc in llm_calls:
                            st.info(f"🧠 Model: `{lc.get('model_name')}` | Provider: `{lc.get('provider')}` | Tokens: `{lc.get('total_tokens')}` (In: {lc.get('input_tokens')}, Out: {lc.get('output_tokens')}) | Latency: `{lc.get('latency_ms', 0):.0f}ms` | Est. Cost: `${lc.get('estimated_cost', 0):.6f}`")

                    rag_events = trace_detail.get("retrieval_events", [])
                    if rag_events:
                        st.markdown("**Retrieval Telemetry:**")
                        for re_ev in rag_events:
                            st.success(f"📚 Method: `{re_ev.get('retrieval_method')}` | Query: `{re_ev.get('query')}` | Retrieved: `{re_ev.get('num_retrieved_docs')}` docs | Latency: `{re_ev.get('retrieval_latency_ms', 0):.1f}ms`")

                    mcp_calls = trace_detail.get("mcp_tool_calls", [])
                    if mcp_calls:
                        st.markdown("**MCP Tool Calls:**")
                        for mc in mcp_calls:
                            st.warning(f"🛠️ Tool: `{mc.get('tool_name')}` | Success: `{mc.get('success')}` | Latency: `{mc.get('duration_ms', 0):.1f}ms`")

                    eval_res = trace_detail.get("evaluation_results", [])
                    if eval_res:
                        st.markdown("**Evaluation Result:**")
                        for ev in eval_res:
                            st.metric("Overall Score", f"{ev.get('overall_score')}/100", help=ev.get("evaluation_reason"))

    with tab_agents:
        st.subheader("Agent Execution Metrics")
        agent_data = await agent_client.aget_observability_agents()
        agents_list = agent_data.get("agents", [])
        if agents_list:
            st.dataframe(agents_list, use_container_width=True)
        else:
            st.info("No agent metrics recorded yet.")

    with tab_rag_mcp:
        st.subheader("MCP Tool Telemetry")
        tool_data = await agent_client.aget_observability_tools()
        tools_list = tool_data.get("tools", [])
        if tools_list:
            st.dataframe(tools_list, use_container_width=True)
        else:
            st.info("No tool executions recorded yet.")

    with tab_llm:
        st.subheader("LLM Token & Cost Summary")
        m_col1, m_col2, m_col3 = st.columns(3)
        m_col1.metric("Total LLM Calls", metrics.get("total_llm_calls", 0))
        m_col2.metric("Total Tokens Consumed", f"{metrics.get('total_tokens', 0):,}")
        m_col3.metric("Estimated Cost (USD)", f"${metrics.get('estimated_cost_usd', 0.0):.6f}")

    with tab_evals:
        st.subheader("Response Quality & Heuristic Evaluations")
        evals_data = await agent_client.aget_observability_evaluations(limit=50)
        evals_list = evals_data.get("evaluations", [])
        if evals_list:
            st.dataframe(evals_list, use_container_width=True)
        else:
            st.info("No response evaluations recorded yet.")


if __name__ == "__main__":
    asyncio.run(main())
