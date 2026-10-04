const BASE_URL = "http://127.0.0.1:8000";

export const uploadFile = async (file) => {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${BASE_URL}/upload`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    throw new Error("Upload failed");
  }

  return response.json();
};

export const analyzeText = async (
  text,
  sourceName,
  sourceType
) => {
  const response = await fetch(`${BASE_URL}/analyze`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      text,
      source_name: sourceName,
      source_type: sourceType,
    }),
  });

  if (!response.ok) {
    throw new Error("Analysis failed");
  }

  return response.json();
};

export const getGraph = async () => {
  const response = await fetch(`${BASE_URL}/graph`);

  if (!response.ok) {
    throw new Error("Could not load graph");
  }

  return response.json();
};

export const getNodeDetails = async (nodeId) => {
  const response = await fetch(
    `${BASE_URL}/graph/node/${nodeId}`
  );

  if (!response.ok) {
    throw new Error("Could not load node details");
  }

  return response.json();
};