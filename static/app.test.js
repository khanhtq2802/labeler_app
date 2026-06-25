/**
 * Tests for the JavaScript changes introduced in this PR:
 *   - showAIAnswer / setAIAnswerBody (DOM helpers)
 *   - askAI streaming logic (NDJSON parsing, accumulation, error handling)
 *
 * Run with:
 *   npx jest static/app.test.js --testEnvironment jsdom
 *
 * Or after installing devDependencies:
 *   npm test
 *
 * Requirements:
 *   npm install --save-dev jest jest-environment-jsdom
 */

/* eslint-env jest */

// ---------------------------------------------------------------------------
// Minimal DOM setup used by every test
// ---------------------------------------------------------------------------

function buildDOM() {
  document.body.innerHTML = `
    <div id="ai-box">
      <div id="ai-answer" hidden></div>
      <button id="ai-ask">Hỏi AI</button>
      <button id="ai-edit-q">Sửa câu hỏi</button>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Inline ports of the changed functions (avoids importing the whole app.js
// which has module-level DOM queries that are hard to satisfy in test env).
// These mirror the PR diff exactly.
// ---------------------------------------------------------------------------

function makeHelpers(answerEl) {
  /**
   * showAIAnswer: recreates the answer container with a close button and body,
   * then delegates to setAIAnswerBody.
   */
  function showAIAnswer(text, isError) {
    answerEl.innerHTML = "";
    const close = document.createElement("button");
    close.className = "ai-answer-close";
    close.type = "button";
    close.textContent = "✕";
    close.title = "Đóng";
    close.addEventListener("click", (e) => { e.stopPropagation(); hideAIAnswer(); });
    const body = document.createElement("div");
    body.className = "ai-answer-body";
    answerEl.appendChild(close);
    answerEl.appendChild(body);
    answerEl.hidden = false;
    setAIAnswerBody(text, isError);
  }

  /**
   * setAIAnswerBody: updates just the body text/class in-place.
   */
  function setAIAnswerBody(text, isError) {
    const body = answerEl.querySelector(".ai-answer-body");
    if (!body) return;
    body.className = isError ? "ai-answer-body ai-answer-err" : "ai-answer-body";
    body.textContent = text;
  }

  function hideAIAnswer() {
    answerEl.hidden = true;
    answerEl.innerHTML = "";
  }

  return { showAIAnswer, setAIAnswerBody, hideAIAnswer };
}

// ---------------------------------------------------------------------------
// handleLine: the NDJSON line processor from askAI.
// Ported verbatim from the PR diff so its logic can be tested in isolation.
// ---------------------------------------------------------------------------

function makeHandleLine(showAIAnswer, setAIAnswerBody, state) {
  /**
   * @param {object} state - { acc: string, failed: bool }
   */
  return function handleLine(line) {
    const trimmed = line.trim();
    if (!trimmed) return;
    let msg;
    try {
      msg = JSON.parse(trimmed);
    } catch {
      return;
    }
    if (msg.error) {
      showAIAnswer("Lỗi: " + msg.error, true);
      state.failed = true;
    } else if (msg.delta) {
      state.acc += msg.delta;
      setAIAnswerBody(state.acc, false);
    }
  };
}

// ---------------------------------------------------------------------------
// Tests: showAIAnswer
// ---------------------------------------------------------------------------

describe("showAIAnswer", () => {
  let answerEl, helpers;

  beforeEach(() => {
    buildDOM();
    answerEl = document.getElementById("ai-answer");
    helpers = makeHelpers(answerEl);
  });

  test("makes the answer element visible", () => {
    helpers.showAIAnswer("hello", false);
    expect(answerEl.hidden).toBe(false);
  });

  test("clears previous content before rendering", () => {
    answerEl.innerHTML = "<span>old</span>";
    helpers.showAIAnswer("new", false);
    expect(answerEl.querySelector("span")).toBeNull();
  });

  test("adds a close button with class ai-answer-close", () => {
    helpers.showAIAnswer("msg", false);
    const btn = answerEl.querySelector(".ai-answer-close");
    expect(btn).not.toBeNull();
    expect(btn.textContent).toBe("✕");
  });

  test("adds an ai-answer-body div with the supplied text", () => {
    helpers.showAIAnswer("the answer text", false);
    const body = answerEl.querySelector(".ai-answer-body");
    expect(body).not.toBeNull();
    expect(body.textContent).toBe("the answer text");
  });

  test("does NOT add ai-answer-err class when isError is false", () => {
    helpers.showAIAnswer("ok", false);
    const body = answerEl.querySelector(".ai-answer-body");
    expect(body.classList.contains("ai-answer-err")).toBe(false);
  });

  test("adds ai-answer-err class when isError is true", () => {
    helpers.showAIAnswer("bad", true);
    const body = answerEl.querySelector(".ai-answer-body");
    expect(body.classList.contains("ai-answer-err")).toBe(true);
    expect(body.classList.contains("ai-answer-body")).toBe(true);
  });

  test("calling twice replaces previous content entirely", () => {
    helpers.showAIAnswer("first", false);
    helpers.showAIAnswer("second", true);
    const bodies = answerEl.querySelectorAll(".ai-answer-body");
    expect(bodies.length).toBe(1);
    expect(bodies[0].textContent).toBe("second");
  });

  test("empty string text is rendered without error", () => {
    helpers.showAIAnswer("", false);
    const body = answerEl.querySelector(".ai-answer-body");
    expect(body.textContent).toBe("");
  });
});

// ---------------------------------------------------------------------------
// Tests: setAIAnswerBody
// ---------------------------------------------------------------------------

describe("setAIAnswerBody", () => {
  let answerEl, helpers;

  beforeEach(() => {
    buildDOM();
    answerEl = document.getElementById("ai-answer");
    helpers = makeHelpers(answerEl);
    // Pre-populate the structure showAIAnswer creates
    helpers.showAIAnswer("initial", false);
  });

  test("updates textContent in-place", () => {
    helpers.setAIAnswerBody("updated text", false);
    expect(answerEl.querySelector(".ai-answer-body").textContent).toBe("updated text");
  });

  test("preserves the close button when updating body", () => {
    helpers.setAIAnswerBody("new", false);
    expect(answerEl.querySelector(".ai-answer-close")).not.toBeNull();
  });

  test("toggles ai-answer-err class off when isError is false", () => {
    helpers.showAIAnswer("err", true);  // set error state
    helpers.setAIAnswerBody("ok now", false);
    const body = answerEl.querySelector(".ai-answer-body");
    expect(body.classList.contains("ai-answer-err")).toBe(false);
  });

  test("adds ai-answer-err class when isError is true", () => {
    helpers.setAIAnswerBody("broken", true);
    const body = answerEl.querySelector(".ai-answer-body");
    expect(body.classList.contains("ai-answer-err")).toBe(true);
  });

  test("no-ops gracefully when .ai-answer-body does not exist", () => {
    answerEl.innerHTML = "";  // remove the body element
    expect(() => helpers.setAIAnswerBody("anything", false)).not.toThrow();
  });

  test("renders unicode text correctly", () => {
    const text = "こんにちは xin chào thế giới";
    helpers.setAIAnswerBody(text, false);
    expect(answerEl.querySelector(".ai-answer-body").textContent).toBe(text);
  });

  test("can be called multiple times incrementally (simulates streaming)", () => {
    let acc = "";
    for (const chunk of ["Hello", " world", "!"]) {
      acc += chunk;
      helpers.setAIAnswerBody(acc, false);
    }
    expect(answerEl.querySelector(".ai-answer-body").textContent).toBe("Hello world!");
  });
});

// ---------------------------------------------------------------------------
// Tests: handleLine (NDJSON line processor from askAI)
// ---------------------------------------------------------------------------

describe("handleLine", () => {
  let answerEl, helpers, state;
  let showCalls, setBodyCalls;

  beforeEach(() => {
    buildDOM();
    answerEl = document.getElementById("ai-answer");

    showCalls = [];
    setBodyCalls = [];

    const mockShow = (text, isError) => { showCalls.push({ text, isError }); };
    const mockSetBody = (text, isError) => { setBodyCalls.push({ text, isError }); };

    state = { acc: "", failed: false };
    helpers = { handleLine: makeHandleLine(mockShow, mockSetBody, state) };
  });

  test("ignores blank lines", () => {
    helpers.handleLine("   ");
    helpers.handleLine("");
    helpers.handleLine("\t\n");
    expect(showCalls.length).toBe(0);
    expect(setBodyCalls.length).toBe(0);
    expect(state.failed).toBe(false);
  });

  test("ignores malformed JSON", () => {
    helpers.handleLine("{not json}");
    helpers.handleLine("undefined");
    expect(showCalls.length).toBe(0);
    expect(state.failed).toBe(false);
  });

  test("accumulates delta chunks", () => {
    helpers.handleLine(JSON.stringify({ delta: "Hello" }));
    helpers.handleLine(JSON.stringify({ delta: " world" }));
    expect(state.acc).toBe("Hello world");
  });

  test("calls setAIAnswerBody with accumulated text on each delta", () => {
    helpers.handleLine(JSON.stringify({ delta: "A" }));
    helpers.handleLine(JSON.stringify({ delta: "B" }));
    expect(setBodyCalls[0]).toEqual({ text: "A", isError: false });
    expect(setBodyCalls[1]).toEqual({ text: "AB", isError: false });
  });

  test("sets failed=true and calls showAIAnswer on error line", () => {
    helpers.handleLine(JSON.stringify({ error: "something broke" }));
    expect(state.failed).toBe(true);
    expect(showCalls.length).toBe(1);
    expect(showCalls[0].isError).toBe(true);
    expect(showCalls[0].text).toContain("something broke");
  });

  test("error message is prefixed with 'Lỗi:'", () => {
    helpers.handleLine(JSON.stringify({ error: "bad thing" }));
    expect(showCalls[0].text).toBe("Lỗi: bad thing");
  });

  test("delta lines after error are still processed (caller checks failed flag)", () => {
    // handleLine itself doesn't stop processing; the caller (askAI loop) checks failed
    helpers.handleLine(JSON.stringify({ error: "oops" }));
    helpers.handleLine(JSON.stringify({ delta: "extra" }));
    expect(state.acc).toBe("extra");
  });

  test("unknown JSON properties are ignored without error", () => {
    helpers.handleLine(JSON.stringify({ unknown: "field" }));
    expect(state.failed).toBe(false);
    expect(showCalls.length).toBe(0);
    expect(setBodyCalls.length).toBe(0);
  });

  test("unicode in delta is preserved", () => {
    const text = "日本語テスト";
    helpers.handleLine(JSON.stringify({ delta: text }));
    expect(state.acc).toBe(text);
    expect(setBodyCalls[0].text).toBe(text);
  });

  test("unicode in error is preserved", () => {
    const msg = "Câu hỏi trống.";
    helpers.handleLine(JSON.stringify({ error: msg }));
    expect(showCalls[0].text).toBe("Lỗi: " + msg);
  });
});

// ---------------------------------------------------------------------------
// Tests: askAI streaming integration (mocking fetch + ReadableStream)
// ---------------------------------------------------------------------------

describe("askAI streaming integration", () => {
  /**
   * Build a mock fetch response whose body is a ReadableStream that yields
   * the given NDJSON lines.
   */
  function makeMockResponse(lines, ok = true, status = 200) {
    const encoder = new TextEncoder();
    const chunks = lines.map((l) => encoder.encode(l + "\n"));
    let i = 0;

    const readable = new ReadableStream({
      pull(controller) {
        if (i < chunks.length) {
          controller.enqueue(chunks[i++]);
        } else {
          controller.close();
        }
      },
    });

    return {
      ok,
      status,
      statusText: ok ? "OK" : "Bad Request",
      body: readable,
      text: async () => lines.join("\n"),
    };
  }

  /**
   * Minimal re-implementation of the askAI streaming loop from the PR,
   * wired to injected dependencies for testability.
   */
  async function runAskAILoop({ fetchResponse, showAIAnswer, setAIAnswerBody }) {
    const res = fetchResponse;
    if (!res.ok) {
      const detail = await res.text().catch(() => "");
      throw new Error(detail || res.statusText);
    }

    showAIAnswer("", false);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let acc = "";
    let failed = false;

    const handleLine = (line) => {
      const trimmed = line.trim();
      if (!trimmed) return;
      let msg;
      try {
        msg = JSON.parse(trimmed);
      } catch {
        return;
      }
      if (msg.error) {
        showAIAnswer("Lỗi: " + msg.error, true);
        failed = true;
      } else if (msg.delta) {
        acc += msg.delta;
        setAIAnswerBody(acc, false);
      }
    };

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        handleLine(buffer.slice(0, nl));
        buffer = buffer.slice(nl + 1);
        if (failed) break;
      }
      if (failed) break;
    }
    if (!failed) {
      handleLine(buffer);
      if (!acc) setAIAnswerBody("(trống)", false);
    }

    return { acc, failed };
  }

  test("accumulates delta chunks into final text", async () => {
    const setBodyCalls = [];
    const response = makeMockResponse([
      JSON.stringify({ delta: "Hello" }),
      JSON.stringify({ delta: " world" }),
    ]);

    const { acc, failed } = await runAskAILoop({
      fetchResponse: response,
      showAIAnswer: jest.fn(),
      setAIAnswerBody: (text, isError) => setBodyCalls.push({ text, isError }),
    });

    expect(failed).toBe(false);
    expect(acc).toBe("Hello world");
    expect(setBodyCalls.at(-1).text).toBe("Hello world");
  });

  test("shows (trống) when stream produces no deltas", async () => {
    const setBodyCalls = [];
    const response = makeMockResponse([]);

    await runAskAILoop({
      fetchResponse: response,
      showAIAnswer: jest.fn(),
      setAIAnswerBody: (text, isError) => setBodyCalls.push({ text, isError }),
    });

    expect(setBodyCalls.at(-1)).toEqual({ text: "(trống)", isError: false });
  });

  test("stops on first error line and marks failed", async () => {
    const showCalls = [];
    const setBodyCalls = [];
    const response = makeMockResponse([
      JSON.stringify({ delta: "partial" }),
      JSON.stringify({ error: "model failed" }),
      JSON.stringify({ delta: "ignored after error" }),
    ]);

    const { failed } = await runAskAILoop({
      fetchResponse: response,
      showAIAnswer: (text, isError) => showCalls.push({ text, isError }),
      setAIAnswerBody: (text, isError) => setBodyCalls.push({ text, isError }),
    });

    expect(failed).toBe(true);
    const errorCall = showCalls.find((c) => c.isError);
    expect(errorCall).toBeDefined();
    expect(errorCall.text).toContain("model failed");
  });

  test("non-ok response throws before reading stream", async () => {
    const response = makeMockResponse([], false, 409);
    await expect(
      runAskAILoop({
        fetchResponse: response,
        showAIAnswer: jest.fn(),
        setAIAnswerBody: jest.fn(),
      })
    ).rejects.toThrow();
  });

  test("incremental setAIAnswerBody calls reflect growing accumulator", async () => {
    const setBodyCalls = [];
    const response = makeMockResponse([
      JSON.stringify({ delta: "A" }),
      JSON.stringify({ delta: "B" }),
      JSON.stringify({ delta: "C" }),
    ]);

    await runAskAILoop({
      fetchResponse: response,
      showAIAnswer: jest.fn(),
      setAIAnswerBody: (text, isError) => setBodyCalls.push(text),
    });

    // Each call should have one more character
    expect(setBodyCalls).toEqual(["A", "AB", "ABC"]);
  });

  test("malformed NDJSON lines are skipped without throwing", async () => {
    const response = makeMockResponse([
      "this is not json",
      JSON.stringify({ delta: "valid" }),
    ]);

    const { acc, failed } = await runAskAILoop({
      fetchResponse: response,
      showAIAnswer: jest.fn(),
      setAIAnswerBody: jest.fn(),
    });

    expect(failed).toBe(false);
    expect(acc).toBe("valid");
  });

  test("showAIAnswer is called once at start to show loading state", async () => {
    const showCalls = [];
    const response = makeMockResponse([JSON.stringify({ delta: "hi" })]);

    await runAskAILoop({
      fetchResponse: response,
      showAIAnswer: (text, isError) => showCalls.push({ text, isError }),
      setAIAnswerBody: jest.fn(),
    });

    // The first call is showAIAnswer("", false) to clear/initialize the panel
    expect(showCalls[0]).toEqual({ text: "", isError: false });
  });
});