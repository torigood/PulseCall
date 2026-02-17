import {
  createPatient,
  getCall,
  listCalls,
  listPatients,
} from "./api";

describe("api client", () => {
  beforeEach(() => {
    jest.resetAllMocks();
  });

  it("lists patients with a GET request", async () => {
    const fetchMock = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ id: "pt_1", name: "Patient A" }],
    });
    global.fetch = fetchMock as unknown as typeof fetch;

    const data = await listPatients();

    expect(data).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/patients",
      expect.objectContaining({
        headers: { "Content-Type": "application/json" },
      }),
    );
  });

  it("creates a patient with POST body", async () => {
    const fetchMock = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ id: "pt_2" }),
    });
    global.fetch = fetchMock as unknown as typeof fetch;

    await createPatient({
      name: "Post-op Patient",
      phone: "+1-555-0100",
      agent_persona: "Nurse",
      conversation_goal: "Check in",
      system_prompt: "Be concise",
      escalation_keywords: ["chest pain"],
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/patients",
      expect.objectContaining({
        method: "POST",
      }),
    );
  });

  it("normalizes call id when backend returns id only", async () => {
    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [{ id: "call_123", summary: "ok" }],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ id: "call_456", summary: "ok" }),
      });
    global.fetch = fetchMock as unknown as typeof fetch;

    const calls = await listCalls();
    const call = await getCall("call_456");

    expect(calls[0].call_id).toBe("call_123");
    expect(call.call_id).toBe("call_456");
  });

  it("throws backend detail on non-ok response", async () => {
    const fetchMock = jest.fn().mockResolvedValue({
      ok: false,
      statusText: "Bad Request",
      json: async () => ({ detail: "Invalid payload" }),
    });
    global.fetch = fetchMock as unknown as typeof fetch;

    await expect(listPatients()).rejects.toThrow("Invalid payload");
  });
});
