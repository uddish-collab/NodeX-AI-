import { useCallback, useEffect, useMemo, useState } from "react";
import "./App.css";
import {
  analyzeText,
  getGraph,
  getNodeDetails,
  getSources,
  uploadFile,
} from "./api";

const buildGraphLayout = (nodes = []) => {
  if (!nodes.length) return [];

  return nodes.map((node, index) => {
    const angle =
      (index / nodes.length) *
      Math.PI *
      2;

    return {
      ...node,
      x: 50 + Math.cos(angle) * 30,
      y: 50 + Math.sin(angle) * 30,
    };
  });
};
/*
 * Readable layout for the graph canvas. Works in pixels, so it needs the canvas size:
 *  1. one cluster per source document, clusters arranged in a grid;
 *  2. the best-connected node of a cluster sits in the middle, the others around it;
 *  3. chips that still overlap are pushed apart, and kept inside the canvas.
 * Returns Map(nodeId -> {x, y}) in percent, or null when the size is not known yet
 * (the caller then keeps the simple circular layout from buildGraphLayout).
 */
const CHIP_HEIGHT = 36; // rendered chips are ~34px tall

// Close to the rendered width (CSS: min 96px, max 200px, 12px bold text).
const chipWidth = (label = "") =>
  Math.min(200, Math.max(96, label.length * 6.4 + 44));

const layoutInCanvas = (nodes, edges, width, height, bottomReserve = 44) => {
  if (!nodes.length || width < 40 || height < 40) return null;

  const size = new Map(
    nodes.map((node) => [node.id, { w: chipWidth(node.label), h: CHIP_HEIGHT }])
  );
  const degree = new Map(nodes.map((node) => [node.id, 0]));

  edges.forEach((edge) => {
    if (degree.has(edge.source)) degree.set(edge.source, degree.get(edge.source) + 1);
    if (degree.has(edge.target)) degree.set(edge.target, degree.get(edge.target) + 1);
  });

  const groups = new Map();
  nodes.forEach((node) => {
    const key = node.source_id || "none";
    groups.set(key, [...(groups.get(key) || []), node]);
  });
  const clusters = [...groups.values()];

  // Prefer cells at least ~260px wide, so side-by-side clusters do not get cramped.
  const maxCols = Math.max(1, Math.floor(width / 260));
  const cols = Math.min(
    maxCols,
    Math.max(1, Math.ceil(Math.sqrt(clusters.length * (width / height))))
  );
  const rows = Math.ceil(clusters.length / cols);
  const cellW = width / cols;
  const cellH = (height - bottomReserve) / rows;

  const pos = new Map();

  clusters.forEach((cluster, index) => {
    const cx = ((index % cols) + 0.5) * cellW;
    const cy = (Math.floor(index / cols) + 0.5) * cellH;
    const [hub, ...rest] = [...cluster].sort(
      (a, b) => degree.get(b.id) - degree.get(a.id)
    );
    const rx = Math.max(70, cellW / 2 - 100);
    const ry = Math.max(60, cellH / 2 - 44);

    pos.set(hub.id, { x: cx, y: cy });
    rest.forEach((node, i) => {
      const angle = -Math.PI / 2 + (i / rest.length) * Math.PI * 2;
      pos.set(node.id, {
        x: cx + Math.cos(angle) * rx,
        y: cy + Math.sin(angle) * ry,
      });
    });
  });

  const ids = nodes.map((node) => node.id);
  const clamp = (value, low, high) => Math.min(Math.max(value, low), high);

  for (let pass = 0; pass < 120; pass += 1) {
    let moved = false;

    for (let a = 0; a < ids.length; a += 1) {
      for (let b = a + 1; b < ids.length; b += 1) {
        const pa = pos.get(ids[a]);
        const pb = pos.get(ids[b]);
        const sa = size.get(ids[a]);
        const sb = size.get(ids[b]);
        const dx = pb.x - pa.x;
        const dy = pb.y - pa.y;
        const overlapX = (sa.w + sb.w) / 2 + 10 - Math.abs(dx);
        const overlapY = (sa.h + sb.h) / 2 + 10 - Math.abs(dy);

        if (overlapX > 0 && overlapY > 0) {
          moved = true;

          if (overlapY < overlapX) {
            const push = ((dy >= 0 ? 1 : -1) * overlapY) / 2;
            pa.y -= push;
            pb.y += push;
          } else {
            const push = ((dx >= 0 ? 1 : -1) * overlapX) / 2;
            pa.x -= push;
            pb.x += push;
          }
        }
      }
    }

    ids.forEach((id) => {
      const point = pos.get(id);
      const box = size.get(id);

      point.x = clamp(point.x, box.w / 2 + 8, width - box.w / 2 - 8);
      point.y = clamp(point.y, box.h / 2 + 8, height - bottomReserve - box.h / 2 + 12);
    });

    if (!moved) break;
  }

  return new Map(
    ids.map((id) => {
      const point = pos.get(id);
      return [id, { x: (point.x / width) * 100, y: (point.y / height) * 100 }];
    })
  );
};

/*
 * Where to put a relationship label on the line between two chips: in the middle of the
 * stretch of line that is NOT hidden behind either chip. Returns null if that gap is too
 * small to hold the label (so labels never cover node names).
 */
const labelSpot = (a, b, text, canvas) => {
  const ax = (a.x / 100) * canvas.w;
  const ay = (a.y / 100) * canvas.h;
  const dx = (b.x / 100) * canvas.w - ax;
  const dy = (b.y / 100) * canvas.h - ay;
  const wa = chipWidth(a.label);
  const wb = chipWidth(b.label);
  const labelWidth = text.length * 5.6 + 20;

  const gapX = Math.abs(dx) - (wa + wb) / 2;
  const gapY = Math.abs(dy) - CHIP_HEIGHT;

  if (gapX < labelWidth + 6 && gapY < 26) return null;

  // Fraction of the line that lies inside each chip's box.
  const inside = (w) =>
    Math.min(
      dx ? w / 2 / Math.abs(dx) : Infinity,
      dy ? CHIP_HEIGHT / 2 / Math.abs(dy) : Infinity
    );
  const start = inside(wa);
  const end = 1 - inside(wb);
  const t = end > start ? (start + end) / 2 : 0.5;

  return {
    x: ((ax + dx * t) / canvas.w) * 100,
    y: ((ay + dy * t) / canvas.h) * 100,
  };
};

// Backend /analyze accepts source_type text | pdf | image.
// DOCX (and TXT/MD) are analyzed as extracted text.
const toSourceType = (kind) =>
  kind === "pdf" || kind === "image" ? kind : "text";

const getNodeTypeLabel = (type) =>
  ({
    document: "DOC",
    person: "USR",
    topic: "TOP",
    project: "PRJ",
    organization: "ORG",
    location: "LOC",
    event: "EVT",
    technology: "TEC",
    deadline: "DUE",
    resource: "RES",
    concept: "CON",
  }[type] || "NODE");

// Defined at module scope (not inside App) so React keeps its state between renders.
function SearchBox({
  compact = false,
  hasGraph,
  searchFocused,
  setSearchFocused,
  searchTerm,
  setSearchTerm,
  setSearchMessage,
  handleSearch,
  clearSearch,
  showSearchResults,
  totalSearchResults,
  documentSearchResults,
  nodeSearchResults,
  handleDocumentSearchResult,
  handleNodeSearchResult,
  getFileType,
}) {
  return (
    <div
      className={`global-search-container ${
        compact ? "compact-search" : ""
      }`}
    >
      <div
        className={`search-input-wrapper ${
          searchFocused ? "search-active" : ""
        }`}
      >
        <span className="search-icon">⌕</span>

        <input
          type="text"
          placeholder="Search documents, people, topics, projects..."
          value={searchTerm}
          onFocus={() => setSearchFocused(true)}
          onChange={(event) => {
            setSearchTerm(event.target.value);
            setSearchMessage("");
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              handleSearch();
            }

            if (event.key === "Escape") {
              clearSearch();
            }
          }}
        />

        {searchTerm && (
          <button
            className="clear-search"
            onClick={clearSearch}
            aria-label="Clear search"
          >
            ×
          </button>
        )}

        {showSearchResults && (
          <div className="search-results">
            {totalSearchResults === 0 ? (
              <div className="search-empty">
                <div className="search-empty-icon">⌕</div>

                <strong>No results found</strong>

                <span>
                  Try a document name, node type, topic,
                  project, or keyword.
                </span>
              </div>
            ) : (
              <>
                {documentSearchResults.length > 0 && (
                  <div className="search-result-group">
                    <div className="search-result-heading">
                      DOCUMENTS
                    </div>

                    {documentSearchResults.map(
                      (document) => (
                        <button
                          key={document.id}
                          className="search-result-item"
                          onMouseDown={(event) =>
                            event.preventDefault()
                          }
                          onClick={() =>
                            handleDocumentSearchResult(
                              document.id
                            )
                          }
                        >
                          <div className="search-result-icon document-result">
                            {getFileType(
                              document.file.name
                            )}
                          </div>

                          <div className="search-result-content">
                            <strong>
                              {document.file.name}
                            </strong>

                            <span>
                              {document.status ===
                              "processed"
                                ? "Processed document"
                                : document.status === "failed"
                                ? "Processing failed"
                                : "Processing document"}
                            </span>
                          </div>

                          <span className="search-result-arrow">
                            →
                          </span>
                        </button>
                      )
                    )}
                  </div>
                )}

                {nodeSearchResults.length > 0 && (
                  <div className="search-result-group">
                    <div className="search-result-heading">
                      KNOWLEDGE NODES
                    </div>

                    {nodeSearchResults.map((node) => (
                      <button
                        key={node.id}
                        className={`search-result-item ${
                          !hasGraph
                            ? "search-result-disabled"
                            : ""
                        }`}
                        onMouseDown={(event) =>
                          event.preventDefault()
                        }
                        onClick={() =>
                          handleNodeSearchResult(
                            node.id
                          )
                        }
                      >
                        <div
                          className={`search-result-node-dot node-type-${node.type}`}
                        ></div>

                        <div className="search-result-content">
                          <strong>
                            {node.label}
                          </strong>

                          <span>
                            {node.type.toUpperCase()}
                          </span>
                        </div>

                        <span className="search-result-arrow">
                          →
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>

      <button
        className="search-button"
        onClick={handleSearch}
      >
        Search
      </button>
    </div>
  );
}

function KnowledgeGraph({
  graph,
  graphError,
  emptyTitle,
  emptyMessage,
  processing,
  selectedNode,
  onSelect,
  full = false,
}) {
  // Measure the canvas so chips can be placed without overlapping.
  const [canvas, setCanvas] = useState(null);
  const [canvasSize, setCanvasSize] = useState({ w: 0, h: 0 });

  // Measure once as soon as the canvas mounts; the observer below keeps it up to date.
  const attachCanvas = useCallback((node) => {
    setCanvas(node);

    if (node) {
      const box = node.getBoundingClientRect();
      setCanvasSize({ w: Math.round(box.width), h: Math.round(box.height) });
    }
  }, []);

  useEffect(() => {
    if (!canvas) return undefined;

    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;

      setCanvasSize((current) =>
        Math.abs(current.w - width) < 1 && Math.abs(current.h - height) < 1
          ? current
          : { w: Math.round(width), h: Math.round(height) }
      );
    });

    observer.observe(canvas);

    return () => observer.disconnect();
  }, [canvas]);

  const nodeTypes = useMemo(
    () => [...new Set(graph.nodes.map((node) => node.type))],
    [graph.nodes]
  );

  const placedNodes = useMemo(() => {
    // Keep chips clear of the legend, which wraps onto more lines when there are many types.
    const legendLines = Math.ceil(
      (nodeTypes.length * 76) / Math.max(120, canvasSize.w * 0.54)
    );
    const spots = layoutInCanvas(
      graph.nodes,
      graph.edges,
      canvasSize.w,
      canvasSize.h,
      28 + 16 * legendLines
    );

    return graph.nodes.map((node) =>
      spots?.get(node.id) ? { ...node, ...spots.get(node.id) } : node
    );
  }, [graph.nodes, graph.edges, canvasSize, nodeTypes]);

  if (!graph.nodes.length) {
    return (
      <div className={`graph-placeholder ${full ? "full-graph-host" : ""}`}>
        <div className="graph-empty-state">
          <div className="graph-empty-icon">◇</div>

          <strong>
            {processing
              ? "Building knowledge map..."
              : emptyTitle || "No knowledge map yet"}
          </strong>

          <span>
            {graphError
              ? graphError
              : processing
              ? "Your graph will appear when processing is complete."
              : emptyMessage ||
                "Upload a document to generate connected knowledge."}
          </span>
        </div>
      </div>
    );
  }

  const nodeById = new Map(placedNodes.map((node) => [node.id, node]));

  return (
    <div
      ref={attachCanvas}
      className={`data-graph ${full ? "data-graph-full" : ""}`}
      // The small dashboard preview grows with the number of nodes so big graphs stay readable.
      style={
        full
          ? undefined
          : {
              minHeight: Math.min(
                720,
                Math.max(360, 200 + graph.nodes.length * 26)
              ),
            }
      }
    >
      <svg
        className="data-graph-svg"
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        {graph.edges.map((edge) => {
          const source = nodeById.get(edge.source);
          const target = nodeById.get(edge.target);

          if (!source || !target) return null;

          const active =
            selectedNode === edge.source || selectedNode === edge.target;

          return (
            <line
              key={edge.id}
              className={`data-edge ${active ? "active" : ""}`}
              x1={source.x}
              y1={source.y}
              x2={target.x}
              y2={target.y}
            />
          );
        })}
      </svg>

      {/* Relationship names for the selected node's connections, only where the gap between
          the two chips is big enough to hold them (the Inspector lists every relationship). */}
      {graph.edges
        .filter(
          (edge) =>
            selectedNode === edge.source || selectedNode === edge.target
        )
        .map((edge) => {
          const source = nodeById.get(edge.source);
          const target = nodeById.get(edge.target);

          if (!source || !target || !canvasSize.w) return null;

          const text = edge.relationship.replace(/_/g, " ");
          const spot = labelSpot(source, target, text, canvasSize);

          if (!spot) return null;

          return (
            <span
              key={`label-${edge.id}`}
              className="data-edge-label"
              style={{ left: `${spot.x}%`, top: `${spot.y}%` }}
            >
              {text}
            </span>
          );
        })}

      {placedNodes.map((node) => (
        <button
          key={node.id}
          type="button"
          title={node.label}
          className={`data-graph-node node-type-${node.type} ${
            selectedNode === node.id ? "selected" : ""
          }`}
          style={{
            left: `${node.x}%`,
            top: `${node.y}%`,
          }}
          onClick={() => onSelect(node.id)}
        >
          <span className="node-type-dot"></span>
          <span className="data-graph-node-label">{node.label}</span>
        </button>
      ))}

      <div className="graph-legend" aria-hidden="true">
        {nodeTypes.map((type) => (
          <span key={type} className={`node-type-${type}`}>
            <span className="node-type-dot"></span>
            {type}
          </span>
        ))}
      </div>

      <div className="graph-helper-text">
        Click a node to inspect its connections.
      </div>
    </div>
  );
}

function NodeInspector({
  graph,
  details,
  detailsLoading,
  detailsError,
  selectedNode,
  full = false,
  onViewMap,
}) {
  const node = graph.nodes.find((item) => item.id === selectedNode);

  if (!node) {
    return (
      <div className={full ? "map-inspector-empty" : "empty-inspector"}>
        <div className="inspector-empty-icon">◇</div>

        <div>
          <h3>{full ? "Select a node" : "Nothing selected"}</h3>

          <p>
            {full
              ? "Click any node in the knowledge map to view its details."
              : "Select a node in the knowledge map to inspect its details."}
          </p>
        </div>
      </div>
    );
  }

  // Relationships and neighbours come from GET /graph/node/{id}.
  const connections = (details?.relationships || []).map((edge) => {
    const otherId = edge.source === node.id ? edge.target : edge.source;

    return {
      ...edge,
      other: details.connected_nodes.find((item) => item.id === otherId),
      otherId,
    };
  });

  const source = details?.source;

  return (
    <div className={full ? "map-inspector-content" : "node-inspector"}>
      <div
        className={
          full ? "map-inspector-header" : "inspector-node-header"
        }
      >
        <div className={`inspector-node-icon type-${node.type}`}>
          {getNodeTypeLabel(node.type)}
        </div>

        <div>
          {full ? (
            <span>{node.type.toUpperCase()}</span>
          ) : (
            <p className="inspector-type">{node.type.toUpperCase()}</p>
          )}

          <h3>{node.label}</h3>
        </div>
      </div>

      <div
        className={
          full ? "map-inspector-divider" : "inspector-divider"
        }
      ></div>

      <div className={full ? "" : "inspector-section"}>
        <span
          className={full ? "map-inspector-label" : "inspector-label"}
        >
          SOURCE
        </span>

        {detailsError ? (
          <p className={full ? "map-description" : ""}>
            Could not load details: {detailsError}
          </p>
        ) : detailsLoading || !source ? (
          <p className={full ? "map-description" : ""}>
            Loading details...
          </p>
        ) : (
          <>
            <p className={full ? "map-description" : ""}>
              <strong>{source.name}</strong>
            </p>

            <p className={full ? "map-description" : ""}>
              {source.excerpt}
            </p>
          </>
        )}
      </div>

      <div className="inspector-stat-line">
        <span>Connections</span>
        <strong>
          {detailsLoading || detailsError ? "-" : connections.length}
        </strong>
      </div>

      <div className={full ? "" : "inspector-section"}>
        <span
          className={full ? "map-inspector-label" : "inspector-label"}
        >
          RELATIONSHIPS
        </span>

        <div className="relationship-list">
          {connections.map((connection) => (
            <div className="relationship-card" key={connection.id}>
              <div className="relationship-top">
                <strong>
                  {connection.other?.label ?? connection.otherId}
                </strong>
                <div className="relationship-meta">
                  <span>{connection.relationship}</span>

                  {typeof connection.confidence === "number" && (
                    <em
                      className="relationship-confidence"
                      title="How strongly the text supports this relationship"
                    >
                      {Math.round(connection.confidence * 100)}%
                    </em>
                  )}
                </div>
              </div>

              <p>{connection.explanation}</p>
            </div>
          ))}
        </div>
      </div>

      {onViewMap && (
        <button
          className="inspector-map-button"
          onClick={onViewMap}
        >
          Open Full Map →
        </button>
      )}
    </div>
  );
}

function App() {
  const [documents, setDocuments] = useState([]);

  // Real graph from the backend (nodes/edges), plus details for the selected node.
  const [graph, setGraph] = useState({ nodes: [], edges: [] });
  const [graphError, setGraphError] = useState(null);
  const [detailsState, setDetailsState] = useState({
    id: null,
    data: null,
    error: null,
  });

  // Saved sources from the backend (GET /sources).
  const [savedSources, setSavedSources] = useState([]);
  const [sourcesState, setSourcesState] = useState({
    loading: true,
    error: null,
  });
  // Selected document: drives the Knowledge Map filter and the "Current" badge.
  const [activeDocumentId, setActiveDocumentId] = useState(null);

  // File shown in the Dashboard upload box. Independent of the selection above.
  const [uploadDocumentId, setUploadDocumentId] = useState(null);

  const [searchTerm, setSearchTerm] = useState("");
  const [selectedNode, setSelectedNode] = useState(null);

  const [activePage, setActivePage] = useState("Dashboard");

  const [profileOpen, setProfileOpen] = useState(false);

  const [searchMessage, setSearchMessage] = useState("");
  // The message bar text that came from a failed processing attempt (styled as an error).
  const [failureMessage, setFailureMessage] = useState(null);
  const [searchFocused, setSearchFocused] = useState(false);

  const [isDragging, setIsDragging] = useState(false);

  const [profileModalOpen, setProfileModalOpen] = useState(false);

  const [settings, setSettings] = useState(() => {
    try {
      const saved = localStorage.getItem("nodex-settings");

      return saved
        ? JSON.parse(saved)
        : {
            theme: "dark",
            graphAnimations: true,
            notifications: true,
            autoProcess: true,
          };
    } catch {
      return {
        theme: "dark",
        graphAnimations: true,
        notifications: true,
        autoProcess: true,
      };
    }
  });

  useEffect(() => {
    localStorage.setItem("nodex-settings", JSON.stringify(settings));
  }, [settings]);

  // Load the saved graph on startup.
  useEffect(() => {
    let cancelled = false;

    getGraph()
      .then((data) => {
        if (cancelled) return;
        setGraph({ nodes: data.nodes, edges: data.edges });
        setGraphError(null);
      })
      .catch((error) => {
        if (!cancelled) setGraphError(error.message);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // Load details whenever a node is selected.
  useEffect(() => {
    if (!selectedNode) return undefined;

    let cancelled = false;

    getNodeDetails(selectedNode)
      .then((data) => {
        if (!cancelled)
          setDetailsState({ id: selectedNode, data, error: null });
      })
      .catch((error) => {
        if (!cancelled)
          setDetailsState({
            id: selectedNode,
            data: null,
            error: error.message,
          });
      });

    return () => {
      cancelled = true;
    };
  }, [selectedNode]);

  // The backend sends no coordinates, so lay the nodes out here.
  const laidOutGraph = useMemo(
    () => ({
      nodes: buildGraphLayout(graph.nodes),
      edges: graph.edges,
    }),
    [graph]
  );

  const selectedDetails =
    detailsState.id === selectedNode ? detailsState : null;
  const detailsLoading = Boolean(selectedNode) && !selectedDetails;

  // Load the saved sources on startup.
  useEffect(() => {
    let cancelled = false;

    getSources()
      .then((data) => {
        if (cancelled) return;
        setSavedSources(data);
        setSourcesState({ loading: false, error: null });
      })
      .catch((error) => {
        if (!cancelled)
          setSourcesState({ loading: false, error: error.message });
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const refreshSources = async () => {
    setSourcesState((current) => ({ ...current, loading: true }));

    try {
      setSavedSources(await getSources());
      setSourcesState({ loading: false, error: null });
    } catch (error) {
      setSourcesState({ loading: false, error: error.message });
    }

    // Per-document counts come from the graph, so reload it too.
    try {
      const data = await getGraph();
      setGraph({ nodes: data.nodes, edges: data.edges });
      setGraphError(null);
    } catch (error) {
      setGraphError(error.message);
    }
  };

  // Documents added this session plus saved sources that are not already
  // among them. Saved-only entries have no File, so `file.size` is null.
  const allDocuments = useMemo(() => {
    const sessionSourceIds = new Set(
      documents.map((document) => document.sourceId).filter(Boolean)
    );

    const savedOnly = savedSources
      .filter((source) => !sessionSourceIds.has(source.id))
      .map((source) => ({
        id: `source-${source.id}`,
        file: { name: source.name, size: null },
        status: "processed",
        error: null,
        sourceId: source.id,
        analysis: null,
        saved: true,
        createdAt: source.created_at,
      }));

    return [...savedOnly, ...documents];
  }, [documents, savedSources]);

  // The selected document (if any) decides which part of the graph the
  // Knowledge Map page shows. With nothing selected it shows everything.
  const scopeDocument =
    allDocuments.find((document) => document.id === activeDocumentId) ||
    null;
  const scopeSourceId = scopeDocument?.sourceId ?? null;

  const mapGraph = useMemo(() => {
    if (!scopeDocument) return laidOutGraph;

    // A document with no source_id yet (still processing, or failed) has no nodes.
    const nodes = graph.nodes.filter(
      (node) => scopeSourceId && node.source_id === scopeSourceId
    );
    const edges = graph.edges.filter(
      (edge) => scopeSourceId && edge.source_id === scopeSourceId
    );

    return { nodes: buildGraphLayout(nodes), edges };
  }, [graph, laidOutGraph, scopeDocument, scopeSourceId]);

  const scopeProcessing = ["uploading", "uploaded", "analyzing"].includes(
    scopeDocument?.status
  );

  const scopeEmptyMessage = scopeDocument
    ? scopeDocument.status === "failed"
      ? `${scopeDocument.file.name} could not be processed: ${scopeDocument.error}`
      : `No knowledge nodes were found in ${scopeDocument.file.name}.`
    : undefined;

  // Node / connection counts for one source, from the loaded graph.
  const countsForDocument = (document) =>
    document.sourceId
      ? {
          nodes: graph.nodes.filter(
            (node) => node.source_id === document.sourceId
          ).length,
          edges: graph.edges.filter(
            (edge) => edge.source_id === document.sourceId
          ).length,
        }
      : {
          nodes: document.analysis?.nodes.length ?? 0,
          edges: document.analysis?.edges.length ?? 0,
        };

  const updateSetting = (key, value) => {
    setSettings((current) => ({
      ...current,
      [key]: value,
    }));
  };

  const resetSettings = () => {
    setSettings({
      theme: "dark",
      graphAnimations: true,
      notifications: true,
      autoProcess: true,
    });

    setSearchMessage("Settings restored to default.");
  };

  const uploadDocument =
    documents.find(
      (document) => document.id === uploadDocumentId
    ) || null;

  const selectedFile = uploadDocument?.file || null;
  const processing =
  uploadDocument?.status === "uploading" ||
  uploadDocument?.status === "uploaded" ||
  uploadDocument?.status === "analyzing";
  const processed = uploadDocument?.status === "processed";
  const failed = uploadDocument?.status === "failed";

  /*
   * =========================
   * SECTION 6 — PROFILE STATS
   * =========================
   */

  const processedDocuments = allDocuments.filter(
    (document) => document.status === "processed"
  ).length;

  const knowledgeNodes = graph.nodes.length;

  const projectsProcessed = graph.nodes.filter(
    (node) => node.type === "project"
  ).length;

  const connectionsCount = graph.edges.length;

  /*
   * =========================
   * SECTION 6 — GLOBAL SEARCH
   * =========================
   */

  const normalizedSearch = searchTerm.trim().toLowerCase();

  const documentSearchResults =
    normalizedSearch.length > 0
      ? allDocuments
          .filter((document) => {
            const fileName = document.file.name.toLowerCase();

            return fileName.includes(normalizedSearch);
          })
          .slice(0, 5)
      : [];

  const nodeSearchResults =
    normalizedSearch.length > 0
      ? graph.nodes
          .filter((node) => {
            const searchableText = `
              ${node.label}
              ${node.type}
            `.toLowerCase();

            return searchableText.includes(normalizedSearch);
          })
          .slice(0, 5)
      : [];

  const totalSearchResults =
    documentSearchResults.length + nodeSearchResults.length;

  const showSearchResults =
    searchFocused && normalizedSearch.length > 0;

  /*
   * =========================
   * FILE HANDLING
   * =========================
   */

  const updateDocument = (documentId, changes) => {
    setDocuments((currentDocuments) =>
      currentDocuments.map((document) =>
        document.id === documentId
          ? { ...document, ...changes }
          : document
      )
    );
  };

  /*
   * Real backend pipeline for one file:
   * uploading -> uploaded -> analyzing -> processed (or failed)
   */
  const processDocument = async (documentId, file) => {
    try {
      const uploaded = await uploadFile(file);

      if (!uploaded.text) {
        throw new Error(
          uploaded.message ||
            "No readable text could be extracted from this file."
        );
      }

      updateDocument(documentId, {
        status: "uploaded",
        extraction: {
          kind: uploaded.kind,
          status: uploaded.extraction_status,
          method: uploaded.extraction_method,
          truncated: uploaded.truncated,
          message: uploaded.message,
        },
      });

      updateDocument(documentId, { status: "analyzing" });

      const result = await analyzeText(
        uploaded.text,
        uploaded.filename || file.name,
        toSourceType(uploaded.kind)
      );

      updateDocument(documentId, {
        status: "processed",
        error: null,
        sourceId: result.source_id,
        analysis: {
          nodes: result.nodes,
          edges: result.edges,
          source_summary: result.source_summary,
        },
      });

      // Refresh the saved sources so the new document is listed by the backend too.
      // (If this fails, the document is still shown from this session.)
      getSources()
        .then((data) => {
          setSavedSources(data);
          setSourcesState({ loading: false, error: null });
        })
        .catch(() => {});

      // Refresh from the backend; if that fails, still show what /analyze returned.
      try {
        const data = await getGraph();
        setGraph({ nodes: data.nodes, edges: data.edges });
        setGraphError(null);
      } catch {
        setGraph((current) => ({
          nodes: [
            ...current.nodes,
            ...result.nodes.filter(
              (node) => !current.nodes.some((item) => item.id === node.id)
            ),
          ],
          edges: [
            ...current.edges,
            ...result.edges.filter(
              (edge) => !current.edges.some((item) => item.id === edge.id)
            ),
          ],
        }));
      }
    } catch (error) {
      updateDocument(documentId, {
        status: "failed",
        error: error.message,
      });

      const message = `${file.name}: ${error.message}`;
      setFailureMessage(message);
      setSearchMessage(message);
    }
  };

  const addFiles = (fileList) => {
    const files = Array.from(fileList || []);

    if (!files.length) return;

    const acceptedExtensions = [
      ".pdf",
      ".png",
      ".jpg",
      ".jpeg",
      ".docx",
      ".txt",
    ];

    const validFiles = files.filter((file) => {
      const lowerName = file.name.toLowerCase();

      return acceptedExtensions.some((extension) =>
        lowerName.endsWith(extension)
      );
    });

    if (!validFiles.length) {
      setSearchMessage(
        "Please upload PDF, image, DOC/DOCX, or TXT files."
      );
      return;
    }

    const newDocuments = validFiles
      .filter(
        (file) =>
          !documents.some(
            (document) =>
              document.file.name === file.name &&
              document.file.size === file.size
          )
      )
      .map((file, index) => ({
        id: `${Date.now()}-${index}-${file.name}`,
        file,
        status: settings.autoProcess ? "uploading" : "ready",
        error: null,
        sourceId: null,
        analysis: null,
        addedAt: Date.now() + index,
      }));

    if (!newDocuments.length) {
      setSearchMessage(
        "Those files are already in your document library."
      );
      return;
    }

    setDocuments((currentDocuments) => [
      ...currentDocuments,
      ...newDocuments,
    ]);

    setActiveDocumentId(newDocuments[0].id);
    setUploadDocumentId(newDocuments[0].id);
    setSelectedNode(null);
    setSearchMessage("");
    setIsDragging(false);

    // Files are processed one after another (not in parallel) to be gentle on the AI API.
    if (settings.autoProcess) {
      (async () => {
        for (const newDocument of newDocuments) {
          await processDocument(newDocument.id, newDocument.file);
        }
      })();
    }
  };

  const handleFileChange = (event) => {
    addFiles(event.target.files);
    event.target.value = "";
  };

  const handleDragOver = (event) => {
    event.preventDefault();
    event.stopPropagation();
    setIsDragging(true);
  };

  const handleDragLeave = (event) => {
    event.preventDefault();
    event.stopPropagation();

    if (
      !event.currentTarget.contains(event.relatedTarget)
    ) {
      setIsDragging(false);
    }
  };

  const handleDrop = (event) => {
    event.preventDefault();
    event.stopPropagation();

    addFiles(event.dataTransfer.files);
  };

  const removeFile = () => {
    if (!uploadDocumentId) return;

    const removedId = uploadDocumentId;

    setDocuments((currentDocuments) => {
      const remaining = currentDocuments.filter(
        (document) => document.id !== removedId
      );

      const nextDocument = remaining[0];

      setUploadDocumentId(nextDocument ? nextDocument.id : null);

      // Keep the selection unless it was the file just removed.
      setActiveDocumentId((current) =>
        current === removedId
          ? nextDocument
            ? nextDocument.id
            : null
          : current
      );

      return remaining;
    });

    setSelectedNode(null);
    setSearchMessage("");
  };

  const deleteDocument = (documentId) => {
    setDocuments((currentDocuments) => {
      const remaining = currentDocuments.filter(
        (document) => document.id !== documentId
      );

      if (documentId === activeDocumentId) {
        const nextDocument = remaining[0];

        setActiveDocumentId(
          nextDocument ? nextDocument.id : null
        );

        setSelectedNode(null);
      }

      if (documentId === uploadDocumentId) {
        setUploadDocumentId(remaining[0] ? remaining[0].id : null);
      }

      return remaining;
    });

    setSearchMessage("");
  };

  /*
   * =========================
   * OPEN DOCUMENT
   * =========================
   *
   * This now actually opens the uploaded
   * file in a new browser tab.
   */

  const openDocument = (documentId) => {
    const document = documents.find(
      (item) => item.id === documentId
    );

    if (!document) return;

    setActiveDocumentId(documentId);
    setSelectedNode(null);
    setSearchMessage("");
    setSearchTerm("");
    setSearchFocused(false);
    setActivePage("Documents");

    try {
      const fileUrl = URL.createObjectURL(document.file);

      window.open(fileUrl, "_blank", "noopener,noreferrer");

      setTimeout(() => {
        URL.revokeObjectURL(fileUrl);
      }, 60000);
    } catch {
      setSearchMessage(
        "The document could not be opened in a new tab."
      );
    }
  };

  const selectDocument = (documentId) => {
    setActiveDocumentId((current) =>
      current === documentId ? null : documentId
    );
    setSelectedNode(null);
    setSearchMessage("");
  };

  const selectNode = (nodeId) => {
    setSelectedNode(nodeId);
    setSearchMessage("");
  };

  /*
   * =========================
   * SECTION 6 — SEARCH ACTIONS
   * =========================
   */

  const clearSearch = () => {
    setSearchTerm("");
    setSelectedNode(null);
    setSearchMessage("");
    setSearchFocused(false);
  };

  const handleDocumentSearchResult = (documentId) => {
    const document = allDocuments.find(
      (item) => item.id === documentId
    );

    if (!document) return;

    setActiveDocumentId(documentId);
    setSelectedNode(null);
    setSearchMessage(`Opened ${document.file.name}`);
    setSearchTerm("");
    setSearchFocused(false);
    setActivePage("Documents");
  };

  const handleNodeSearchResult = (nodeId) => {
    const node = graph.nodes.find(
      (item) => item.id === nodeId
    );

    if (!node) return;

    if (graph.nodes.length === 0) {
      setSearchMessage(
        "Upload and process a document before exploring knowledge nodes."
      );
      return;
    }

    if (scopeDocument && node.source_id !== scopeSourceId) {
      setActiveDocumentId(null);
    }

    setSelectedNode(nodeId);
    setSearchMessage(`Showing ${node.label}`);
    setSearchTerm("");
    setSearchFocused(false);
    setActivePage("Knowledge Map");
  };

  const handleSearch = () => {
    const search = searchTerm.trim().toLowerCase();

    if (!search) {
      setSelectedNode(null);
      setSearchMessage("");
      return;
    }

    if (
      documentSearchResults.length > 0 ||
      nodeSearchResults.length > 0
    ) {
      if (documentSearchResults.length > 0) {
        handleDocumentSearchResult(
          documentSearchResults[0].id
        );
        return;
      }

      if (
        nodeSearchResults.length > 0 &&
        processedDocuments > 0
      ) {
        handleNodeSearchResult(nodeSearchResults[0].id);
        return;
      }
    }

    setSelectedNode(null);
    setSearchMessage(
      `No results found for "${searchTerm.trim()}".`
    );
  };

  /*
   * =========================
   * NAVIGATION
   * =========================
   */

  const navigateTo = (page) => {
    setActivePage(page);
    setProfileOpen(false);
    setSearchFocused(false);
  };

  /*
   * =========================
   * FILE TYPE
   * =========================
   */

  const getFileType = (name) => {
    const lower = name.toLowerCase();

    if (lower.endsWith(".pdf")) return "PDF";

    if (
      [".jpg", ".jpeg", ".png"].some((ext) =>
        lower.endsWith(ext)
      )
    ) {
      return "IMG";
    }

    if (
      [".doc", ".docx"].some((ext) =>
        lower.endsWith(ext)
      )
    ) {
      return "DOC";
    }

    if (lower.endsWith(".txt")) return "TXT";

    return "FILE";
  };

  /*
   * =========================
   * SEARCH COMPONENT PROPS
   * =========================
   */

  const searchBoxProps = {
    hasGraph: graph.nodes.length > 0,
    searchFocused,
    setSearchFocused,
    searchTerm,
    setSearchTerm,
    setSearchMessage,
    handleSearch,
    clearSearch,
    showSearchResults,
    totalSearchResults,
    documentSearchResults,
    nodeSearchResults,
    handleDocumentSearchResult,
    handleNodeSearchResult,
    getFileType,
  };

  return (
    <div
      className={`app theme-${settings.theme} ${
        settings.graphAnimations
          ? "animations-on"
          : "animations-off"
      }`}
    >
      {/* =========================
          SIDEBAR
      ========================= */}

      <aside className="sidebar">
        <div className="logo">
          <span className="logo-icon">N</span>
          <span>NodeX</span>
        </div>

        <nav>
          {[
            "Dashboard",
            "Knowledge Map",
            "Documents",
          ].map((page) => (
            <button
              key={page}
              className={`nav-item ${
                activePage === page ? "active" : ""
              }`}
              onClick={() => navigateTo(page)}
            >
              {page}
            </button>
          ))}
        </nav>

        <div className="sidebar-bottom">
          <button
            className={`nav-item ${
              activePage === "Settings" ? "active" : ""
            }`}
            onClick={() => navigateTo("Settings")}
          >
            Settings
          </button>
        </div>
      </aside>

      {/* =========================
          MAIN CONTENT
      ========================= */}

      <main className="main-content">
        {/* =========================
            HEADER
        ========================= */}

        <header className="topbar">
          <div>
            <p className="small-text">WELCOME BACK</p>

            <h1>
              {activePage === "Dashboard" &&
                "Knowledge Dashboard"}

              {activePage === "Knowledge Map" &&
                "Knowledge Map"}

              {activePage === "Documents" &&
                "Your Documents"}

              {activePage === "Settings" &&
                "Settings"}
            </h1>
          </div>

          <div className="topbar-actions">
            {activePage !== "Dashboard" && (
              <SearchBox compact {...searchBoxProps} />
            )}

            {/* PROFILE */}

            <div className="profile-container">
              <button
                className="profile"
                onClick={() => {
                  setProfileOpen(!profileOpen);
                  setSearchFocused(false);
                }}
              >
                <div className="profile-avatar">U</div>

                <span>User</span>

                <span className="profile-arrow">
                  {profileOpen ? "⌃" : "⌄"}
                </span>
              </button>

              {profileOpen && (
                <div className="profile-menu profile-card">
                  <div className="profile-card-header">
                    <div className="profile-card-avatar">
                      U
                    </div>

                    <div>
                      <strong>User</strong>
                      <span>
                        NodeX Workspace Member
                      </span>
                    </div>
                  </div>

                  <div className="profile-card-divider"></div>

                  <div className="profile-card-role">
                    <span>ROLE</span>
                    <strong>Workspace Member</strong>
                  </div>

                  <div className="profile-stats-grid">
                    <div className="profile-stat">
                      <strong>
                        {projectsProcessed}
                      </strong>
                      <span>Projects</span>
                    </div>

                    <div className="profile-stat">
                      <strong>
                        {processedDocuments}
                      </strong>
                      <span>Documents</span>
                    </div>

                    <div className="profile-stat">
                      <strong>
                        {knowledgeNodes}
                      </strong>
                      <span>Nodes</span>
                    </div>
                  </div>

                  <button
                    className="profile-view-button"
                    onClick={() => {
                      setProfileOpen(false);
                      setProfileModalOpen(true);
                    }}
                  >
                    View Full Profile →
                  </button>
                </div>
              )}
            </div>
          </div>
        </header>

        {/* =========================
            DASHBOARD
        ========================= */}

        {activePage === "Dashboard" && (
          <>
            <SearchBox {...searchBoxProps} />

            {searchMessage && (
              <div
                className={`search-message search-feedback ${
                  searchMessage === failureMessage
                    ? "search-feedback-error"
                    : ""
                }`}
              >
                <span>
                  {searchMessage === failureMessage ? "✕" : "✓"}
                </span>
                {searchMessage}
              </div>
            )}

            {/* STATS */}

            <section className="stats">
              <div className="stat-card">
                <span>Documents</span>
                <strong>{allDocuments.length}</strong>
              </div>

              <div className="stat-card">
                <span>Knowledge Nodes</span>
                <strong>{knowledgeNodes}</strong>
              </div>

              <div className="stat-card">
                <span>Connections</span>
                <strong>{connectionsCount}</strong>
              </div>
            </section>

            {/* MAIN DASHBOARD */}

            <section className="dashboard-grid">
              {/* UPLOAD */}

              <div className="panel upload-panel">
                <div className="panel-header">
                  <div>
                    <p className="panel-label">INPUT</p>
                    <h2>Add Information</h2>
                  </div>

                  {documents.length > 0 && (
                    <span className="upload-count-badge">
                      {documents.length}{" "}
                      {documents.length === 1
                        ? "document"
                        : "documents"}
                    </span>
                  )}
                </div>

                <div
                  className={`upload-box ${
                    processing ? "is-processing" : ""
                  } ${
                    processed ? "is-processed" : ""
                  } ${
                    failed ? "is-failed" : ""
                  } ${
                    isDragging ? "is-dragging" : ""
                  }`}
                  onDragOver={handleDragOver}
                  onDragEnter={handleDragOver}
                  onDragLeave={handleDragLeave}
                  onDrop={handleDrop}
                >
                  <div className="upload-icon">
                    {isDragging
                      ? "↓"
                      : processing
                      ? "◌"
                      : processed
                      ? "✓"
                      : failed
                      ? "!"
                      : "↑"}
                  </div>

                  {isDragging ? (
                    <>
                      <h3>Drop your files here</h3>

                      <p>
                        Release to add your documents to
                        the knowledge base.
                      </p>
                    </>
                  ) : !selectedFile ? (
                    <>
                      <h3>Drop documents here</h3>

                      <p>
                        Drag and drop one or multiple files,
                        or browse your computer.
                      </p>
                    </>
                  ) : processing ? (
                    <>
                      <h3>Processing document...</h3>

                      <p>
                        Extracting information and preparing
                        your knowledge map.
                      </p>

                      <div className="processing-bar">
                        <span></span>
                      </div>

                      <span className="upload-hint processing-hint">
                        This can take up to a minute if the AI service is busy.
                      </span>
                    </>
                  ) : (
                    <>
                      <h3>
                        {processed
                          ? "Document processed"
                          : failed
                          ? "Processing failed"
                          : "Document selected"}
                      </h3>

                      <p
                        className={
                          failed ? "upload-error-text" : undefined
                        }
                        title={failed ? uploadDocument.error : undefined}
                      >
                        {processed
                          ? "Your knowledge map is ready to explore."
                          : failed
                          ? uploadDocument.error.length > 140
                            ? `${uploadDocument.error
                                .slice(0, 140)
                                .trimEnd()}…`
                            : uploadDocument.error
                          : "Your document is ready to be processed."}
                      </p>
                    </>
                  )}

                  <input
                    type="file"
                    id="file-upload"
                    style={{ display: "none" }}
                    accept=".pdf,.png,.jpg,.jpeg,.docx,.txt"
                    multiple
                    onChange={handleFileChange}
                  />

                  <label
                    htmlFor="file-upload"
                    className="upload-button"
                  >
                    {documents.length > 0
                      ? "Add More Files"
                      : "Choose Files"}
                  </label>

                  <span className="upload-hint">
                    PDF · PNG · JPG · DOCX · TXT
                  </span>

                  {selectedFile && (
                    <div className="selected-file">
                      <div className="selected-file-info">
                        <div className="selected-file-icon">
                          {processed ? "✓" : failed ? "!" : "…"}
                        </div>

                        <div className="selected-file-details">
                          <strong>
                            {selectedFile.name}
                          </strong>

                          <span>
                            {(
                              selectedFile.size /
                              1024 /
                              1024
                            ).toFixed(2)}{" "}
                            MB
                            {" • "}
                            {processing
                              ? "Processing..."
                              : processed
                              ? "Processed"
                              : failed
                              ? "Failed"
                              : "Ready to process"}
                          </span>
                        </div>
                      </div>

                      {!processing && (
                        <button
                          className="remove-file"
                          onClick={removeFile}
                          aria-label="Remove current file"
                        >
                          ×
                        </button>
                      )}
                    </div>
                  )}

                  {documents.length > 1 && (
                    <div className="upload-library-note">
                      <span>✓</span>
                      {documents.length} files are safely in
                      your library.
                    </div>
                  )}
                </div>
              </div>

              {/* KNOWLEDGE MAP */}

              <div className="panel graph-panel">
                <div className="panel-header">
                  <div>
                    <p className="panel-label">
                      VISUALIZATION
                    </p>

                    <h2>Knowledge Map</h2>
                  </div>

                  <button
                    className="view-button"
                    onClick={() =>
                      setActivePage("Knowledge Map")
                    }
                  >
                    View Full Map
                  </button>
                </div>

                <KnowledgeGraph
                  graph={laidOutGraph}
                  graphError={graphError}
                  processing={processing}
                  selectedNode={selectedNode}
                  onSelect={selectNode}
                />
              </div>

              {/* CONNECTION INSPECTOR */}

              <div className="panel inspector-panel">
                <div className="panel-header">
                  <div>
                    <p className="panel-label">
                      INSPECTOR
                    </p>

                    <h2>Connection Details</h2>
                  </div>
                </div>

                <div className="inspector-content">
                  <NodeInspector
                    graph={laidOutGraph}
                    details={selectedDetails?.data}
                    detailsLoading={detailsLoading}
                    detailsError={selectedDetails?.error}
                    selectedNode={selectedNode}
                    onViewMap={() =>
                      setActivePage("Knowledge Map")
                    }
                  />
                </div>
              </div>
            </section>
          </>
        )}

        {/* =========================
            KNOWLEDGE MAP
        ========================= */}

        {activePage === "Knowledge Map" && (
          <section className="page-placeholder section6-fade">
            <div className="map-page-header">
              <div>
                <p className="panel-label">
                  VISUALIZATION
                </p>

                <h2>Knowledge Map</h2>

                <p className="map-summary">
                  {mapGraph.nodes.length} nodes · {mapGraph.edges.length}{" "}
                  connections
                </p>
              </div>

              <button
                className="back-dashboard-button"
                onClick={() =>
                  setActivePage("Dashboard")
                }
              >
                ← Dashboard
              </button>
            </div>

            {scopeDocument && graph.nodes.length > 0 && (
              <div className="map-scope-bar">
                <span>
                  Showing the map for{" "}
                  <strong>{scopeDocument.file.name}</strong>
                </span>

                <button
                  className="view-button"
                  onClick={() => setActiveDocumentId(null)}
                >
                  Show all documents
                </button>
              </div>
            )}

            {graph.nodes.length === 0 ? (
              <div className="dashboard-empty-state">
                <div className="dashboard-empty-icon">
                  ◇
                </div>

                <h3>Your knowledge map is waiting</h3>

                <p>
                  Upload and process a document to start
                  exploring connected knowledge.
                </p>

                <button
                  className="upload-button"
                  onClick={() =>
                    setActivePage("Dashboard")
                  }
                >
                  Upload a Document
                </button>
              </div>
            ) : (
              <div className="knowledge-map-layout">
                <div className="full-map-placeholder">
                  <KnowledgeGraph
                    graph={mapGraph}
                    graphError={graphError}
                    emptyTitle={
                      scopeDocument ? "No nodes for this document" : undefined
                    }
                    emptyMessage={scopeEmptyMessage}
                    processing={scopeDocument ? scopeProcessing : processing}
                    selectedNode={selectedNode}
                    onSelect={selectNode}
                    full
                  />
                </div>

                <div className="map-inspector">
                  <NodeInspector
                    graph={laidOutGraph}
                    details={selectedDetails?.data}
                    detailsLoading={detailsLoading}
                    detailsError={selectedDetails?.error}
                    selectedNode={selectedNode}
                    full
                  />
                </div>
              </div>
            )}
          </section>
        )}

        {/* =========================
            DOCUMENTS
        ========================= */}

        {activePage === "Documents" && (
          <section className="page-placeholder section6-fade">
            <div className="documents-page-header">
              <div>
                <p className="panel-label">DOCUMENTS</p>
                <h2>Your Documents</h2>
              </div>

              <button
                className="upload-button"
                onClick={() =>
                  setActivePage("Dashboard")
                }
              >
                + Upload
              </button>
            </div>

            {allDocuments.length === 0 && sourcesState.loading ? (
              <div className="empty-page">
                <span>□</span>

                <h3>Loading your documents...</h3>

                <p>Fetching saved documents from the backend.</p>
              </div>
            ) : allDocuments.length === 0 && sourcesState.error ? (
              <div className="empty-page">
                <span>□</span>

                <h3>Could not load saved documents</h3>

                <p>{sourcesState.error}</p>

                <button
                  className="upload-button"
                  onClick={refreshSources}
                >
                  Try again
                </button>
              </div>
            ) : allDocuments.length === 0 ? (
              <div className="empty-page">
                <span>□</span>

                <h3>No documents yet</h3>

                <p>
                  Upload a document to start building your
                  knowledge base.
                </p>

                <button
                  className="upload-button"
                  onClick={() =>
                    setActivePage("Dashboard")
                  }
                >
                  Upload a Document
                </button>
              </div>
            ) : (
              <div className="documents-list">
                {sourcesState.error && (
                  <span className="upload-hint">
                    Could not refresh saved documents:{" "}
                    {sourcesState.error}
                  </span>
                )}

                {allDocuments.map((document) => {
                  const isActive =
                    document.id === activeDocumentId;

                  const isProcessing = [
                    "uploading",
                    "uploaded",
                    "analyzing",
                  ].includes(document.status);

                  const isProcessed =
                    document.status === "processed";

                  const isFailed = document.status === "failed";

                  return (
                    <div
                      className={`document-card ${
                        isActive
                          ? "active-document"
                          : ""
                      }`}
                      key={document.id}
                      data-status={document.status}
                      onClick={() =>
                        selectDocument(document.id)
                      }
                    >
                      <div className="document-info">
                        <div className="document-icon">
                          {getFileType(
                            document.file.name
                          )}
                        </div>

                        <div className="document-details">
                          <strong>
                            {document.file.name}
                          </strong>

                          <span>
                            {document.saved
                              ? `Saved ${new Date(
                                  document.createdAt
                                ).toLocaleDateString()}`
                              : `${(
                                  document.file.size /
                                  1024 /
                                  1024
                                ).toFixed(2)} MB`}
                            {" • "}
                            {getFileType(
                              document.file.name
                            )}
                          </span>

                          <small>
                            {isProcessing
                              ? "Processing document..."
                              : isProcessed
                              ? `Processed • ${countsForDocument(document).nodes} nodes • ${countsForDocument(document).edges} connections`
                              : isFailed
                              ? `Failed • ${document.error}`
                              : "Ready to process"}
                          </small>
                        </div>
                      </div>

                      <div className="document-card-actions">
                        {isActive && (
                          <span className="active-document-badge">
                            Current
                          </span>
                        )}

                        {!document.saved && (
                          <button
                            className="document-open-button"
                            onClick={(event) => {
                              event.stopPropagation();

                              openDocument(
                                document.id
                              );
                            }}
                          >
                            Open
                          </button>
                        )}

                        {!document.saved && (
                          <button
                            className="remove-file"
                            onClick={(event) => {
                              event.stopPropagation();

                              deleteDocument(
                                document.id
                              );
                            }}
                            aria-label={`Delete ${document.file.name}`}
                          >
                            ×
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        )}

        {/* =========================
            SETTINGS
        ========================= */}

        {activePage === "Settings" && (
          <section className="settings-page section6-fade">
            <div className="settings-intro">
              <div>
                <p className="panel-label">WORKSPACE</p>

                <h2>Settings</h2>

                <p>
                  Customize how NodeX looks, feels, and
                  handles your knowledge workspace.
                </p>
              </div>

              <button
                className="settings-reset-button"
                onClick={resetSettings}
              >
                Reset Defaults
              </button>
            </div>

            <div className="settings-grid">
              <div className="settings-section-card">
                <div className="settings-section-heading">
                  <div className="settings-section-icon">
                    ◐
                  </div>

                  <div>
                    <h3>Appearance</h3>
                    <span>
                      Control the visual style of your
                      workspace.
                    </span>
                  </div>
                </div>

                <div className="setting-control-row">
                  <div className="setting-control-copy">
                    <strong>Theme</strong>

                    <span>
                      Choose the interface appearance.
                    </span>
                  </div>

                  <div className="theme-options">
                    {[
                      ["dark", "Dark"],
                      ["dim", "Dim"],
                      ["light", "Light"],
                    ].map(([value, label]) => (
                      <button
                        key={value}
                        className={`theme-option ${
                          settings.theme === value
                            ? "active"
                            : ""
                        }`}
                        onClick={() =>
                          updateSetting(
                            "theme",
                            value
                          )
                        }
                      >
                        <span
                          className={`theme-preview ${value}`}
                        ></span>

                        {label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="setting-control-row">
                  <div className="setting-control-copy">
                    <strong>
                      Graph animations
                    </strong>

                    <span>
                      Keep node and connection interactions
                      smooth and animated.
                    </span>
                  </div>

                  <button
                    className={`toggle ${
                      settings.graphAnimations
                        ? "on"
                        : ""
                    }`}
                    onClick={() =>
                      updateSetting(
                        "graphAnimations",
                        !settings.graphAnimations
                      )
                    }
                  >
                    <span></span>
                  </button>
                </div>
              </div>

              <div className="settings-section-card">
                <div className="settings-section-heading">
                  <div className="settings-section-icon">
                    ◇
                  </div>

                  <div>
                    <h3>Processing</h3>

                    <span>
                      Configure document processing
                      behavior.
                    </span>
                  </div>
                </div>

                <div className="setting-control-row">
                  <div className="setting-control-copy">
                    <strong>
                      Auto process uploads
                    </strong>

                    <span>
                      Automatically process new documents
                      after they are added.
                    </span>
                  </div>

                  <button
                    className={`toggle ${
                      settings.autoProcess
                        ? "on"
                        : ""
                    }`}
                    onClick={() =>
                      updateSetting(
                        "autoProcess",
                        !settings.autoProcess
                      )
                    }
                  >
                    <span></span>
                  </button>
                </div>

                <div className="setting-info-box">
                  <span>i</span>

                  <p>
                    Backend integration will replace this
                    demo processing with real document
                    analysis later.
                  </p>
                </div>
              </div>

              <div className="settings-section-card">
                <div className="settings-section-heading">
                  <div className="settings-section-icon">
                    !
                  </div>

                  <div>
                    <h3>Notifications</h3>

                    <span>
                      Choose whether NodeX should show
                      status feedback.
                    </span>
                  </div>
                </div>

                <div className="setting-control-row">
                  <div className="setting-control-copy">
                    <strong>
                      Processing notifications
                    </strong>

                    <span>
                      Show helpful status messages when
                      documents finish processing.
                    </span>
                  </div>

                  <button
                    className={`toggle ${
                      settings.notifications
                        ? "on"
                        : ""
                    }`}
                    onClick={() =>
                      updateSetting(
                        "notifications",
                        !settings.notifications
                      )
                    }
                  >
                    <span></span>
                  </button>
                </div>
              </div>

              {/* PROFILE */}

              <div className="settings-section-card">
                <div className="settings-section-heading">
                  <div className="settings-section-icon">
                    U
                  </div>

                  <div>
                    <h3>Profile</h3>

                    <span>
                      Your current NodeX workspace profile.
                    </span>
                  </div>
                </div>

                <div className="profile-settings-row">
                  <div className="settings-profile-avatar">
                    U
                  </div>

                  <div>
                    <strong>User</strong>

                    <span>
                      NodeX workspace member
                    </span>
                  </div>

                  <button
                    className="secondary-settings-button"
                    onClick={() =>
                      setProfileModalOpen(true)
                    }
                  >
                    View Profile
                  </button>
                </div>
              </div>
            </div>

            <div className="settings-saved">
              <span>✓</span>

              Settings are saved automatically in this
              browser.
            </div>
          </section>
        )}

        {/* =========================
            SECTION 6 — PROFILE MODAL
        ========================= */}

        {profileModalOpen && (
          <div
            className="profile-overlay"
            onMouseDown={(event) => {
              if (
                event.target === event.currentTarget
              ) {
                setProfileModalOpen(false);
              }
            }}
          >
            <div className="profile-modal">
              <div className="profile-modal-header">
                <div>
                  <p className="panel-label">PROFILE</p>
                  <h2>Your NodeX Profile</h2>
                </div>

                <button
                  className="profile-modal-close"
                  onClick={() =>
                    setProfileModalOpen(false)
                  }
                  aria-label="Close profile"
                >
                  ×
                </button>
              </div>

              <div className="profile-modal-user">
                <div className="profile-modal-avatar">
                  U
                </div>

                <div>
                  <h3>User</h3>

                  <p>
                    NodeX Workspace Member
                  </p>

                  <span>
                    Personal knowledge workspace
                  </span>
                </div>
              </div>

              <div className="profile-modal-divider"></div>

              <div className="profile-modal-role">
                <span>ROLE</span>

                <strong>
                  Workspace Member
                </strong>
              </div>

              <div className="profile-modal-stats">
                <div className="profile-modal-stat">
                  <strong>
                    {projectsProcessed}
                  </strong>

                  <span>Projects Processed</span>
                </div>

                <div className="profile-modal-stat">
                  <strong>
                    {processedDocuments}
                  </strong>

                  <span>Documents Processed</span>
                </div>

                <div className="profile-modal-stat">
                  <strong>
                    {knowledgeNodes}
                  </strong>

                  <span>Knowledge Nodes</span>
                </div>

                <div className="profile-modal-stat">
                  <strong>
                    {connectionsCount}
                  </strong>

                  <span>Connections</span>
                </div>
              </div>

              <div className="profile-modal-footer">
                <button
                  className="secondary-settings-button"
                  onClick={() => {
                    setProfileModalOpen(false);
                    setActivePage("Settings");
                  }}
                >
                  Profile Settings
                </button>

                <button
                  className="upload-button"
                  onClick={() =>
                    setProfileModalOpen(false)
                  }
                >
                  Done
                </button>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

export default App;