// Backend base URL. Override with VITE_API_URL (see .env.example).
export const BASE_URL = (
  import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"
).replace(/\/+$/, "");

// Turn a failed response into a readable message, preferring the backend's `detail`.
// `detail` is a string for NodeX errors and a list of {loc, msg} for validation (422) errors.
const errorMessage = async (response, fallback) => {
  try {
    const { detail } = await response.json();

    if (typeof detail === "string" && detail) return detail;

    if (Array.isArray(detail) && detail.length) {
      return detail
        .map((item) => {
          const field = Array.isArray(item.loc)
            ? item.loc.filter((part) => part !== "body").join(".")
            : "";

          return field ? `${field}: ${item.msg}` : item.msg;
        })
        .join("; ");
    }
  } catch {
    // body was not JSON; fall through to the generic message
  }

  return `${fallback} (HTTP ${response.status})`;
};

const request = async (path, options, fallback) => {
  let response;

  try {
    response = await fetch(`${BASE_URL}${path}`, options);
  } catch {
    throw new Error(
      `Could not reach the NodeX backend at ${BASE_URL}. Is it running?`
    );
  }

  if (!response.ok) {
    throw new Error(await errorMessage(response, fallback));
  }

  return response.json();
};

export const uploadFile = async (file) => {
  const formData = new FormData();
  formData.append("file", file);

  return request(
    "/upload",
    { method: "POST", body: formData },
    "Upload failed"
  );
};

export const analyzeText = async (text, sourceName, sourceType) =>
  request(
    "/analyze",
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        text,
        source_name: sourceName,
        source_type: sourceType,
      }),
    },
    "Analysis failed"
  );

export const getGraph = async () =>
  request("/graph", undefined, "Could not load graph");

export const getNodeDetails = async (nodeId) =>
  request(
    `/graph/node/${encodeURIComponent(nodeId)}`,
    undefined,
    "Could not load node details"
  );

export const getSources = async () =>
  request("/sources", undefined, "Could not load saved documents");
