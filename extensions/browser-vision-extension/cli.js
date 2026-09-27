"use strict";

const VALID_TOOLS = ["view", "click", "type", "scroll"];

function isJsonObject(value) {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value)
  );
}

function parseBvCommand(argv) {
  if (!Array.isArray(argv) || argv.length < 1) {
    return { error: "MISSING_PARAMS_JSON" };
  }

  const tool = argv[0];

  if (!VALID_TOOLS.includes(tool)) {
    return {
      tool,
      error: "UNKNOWN_TOOL",
      detail: `Unknown tool: ${tool}`,
    };
  }

  const flagIndex = argv.indexOf("--params-json");

  if (flagIndex === -1) {
    return {
      tool,
      error: "MISSING_PARAMS_JSON",
    };
  }

  const paramsJson = argv[flagIndex + 1];

  if (paramsJson === undefined) {
    return {
      tool,
      error: "MISSING_PARAMS_JSON",
    };
  }

  try {
    const parsed = JSON.parse(paramsJson);

    if (!isJsonObject(parsed)) {
      return {
        tool,
        error: "INVALID_PARAMS_JSON",
        detail: "params-json must be a JSON object",
      };
    }

    return { tool, paramsJson };
  } catch (err) {
    return {
      tool,
      error: "INVALID_PARAMS_JSON",
      detail: err.message,
    };
  }
}

async function dispatchBvCommand(handlers, tool, paramsJson) {
  if (!handlers || typeof handlers !== "object" || !handlers[tool]) {
    return {
      error: "UNKNOWN_TOOL",
      detail: `Unknown tool: ${tool}`,
    };
  }

  let params;

  try {
    params = JSON.parse(paramsJson);

    if (!isJsonObject(params)) {
      return {
        error: "INVALID_PARAMS_JSON",
        detail: "params-json must be a JSON object",
      };
    }
  } catch (err) {
    return {
      error: "INVALID_PARAMS_JSON",
      detail: err.message,
    };
  }

  const result = await handlers[tool](params);

  if (result && result.error) {
    return result;
  }

  return result;
}

module.exports = {
  parseBvCommand,
  dispatchBvCommand,
};
