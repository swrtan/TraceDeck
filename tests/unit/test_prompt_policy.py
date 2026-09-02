from tracedeck.prompt_policy import prompt_for_storage


def test_ambient_browser_context_is_not_retained():
    prompt = """<in-app-browser-context source=\"ambient-ui-state\">
- Current URL: http://127.0.0.1:8765/
</in-app-browser-context>

## My request:
bi deneyelim"""

    assert prompt_for_storage(prompt) == "bi deneyelim"


def test_attachment_wrapper_keeps_safe_name_and_real_request_only():
    prompt = """# Files pasted by the user:

## \"brief.pdf\": C:/Users/test/brief.pdf

## My request:
briefi üç maddede özetle"""

    assert prompt_for_storage(prompt) == "[Attached: brief.pdf]\nbriefi üç maddede özetle"


def test_pasted_codex_history_is_not_a_prompt():
    prompt = "The following is the Codex agent history added since your last approval assessment. Continue the same review conversation."

    assert prompt_for_storage(prompt) is None


def test_attached_pasted_codex_history_is_not_a_prompt():
    prompt = "[Attached: pasted-text.txt]\nThe following is the Codex agent history added since your last approval assessment."

    assert prompt_for_storage(prompt) is None


def test_attached_approval_transcript_variant_is_not_a_prompt():
    prompt = """[Attached: screenshot.png] The following is the Codex agent history whose request action you are assessing.
    >>> TRANSCRIPT START
    [1] user: gerçek kullanıcı isteği
    >>> TRANSCRIPT END
    >>> APPROVAL REQUEST START
    {\"command\": [\"local check\"]}
    >>> APPROVAL REQUEST END"""

    assert prompt_for_storage(prompt) is None
