### Assumptions

1. Refinements are stored in memory (or a JSON file) keyed by a string/int `id`. Since the actual storage mechanism isn't visible, I assume refinements are persisted in a JSON file at a path like `data/refinements.json`, where each refinement object has at minimum: `id`, `module` (filename under `knowledge-core/`), `field`, `suggested_value`, and an optional `status` field (default `None`, set to `"accepted"` or `"rejected"`).
2. The knowledge-core module files are Markdown with YAML frontmatter. "Applying" a refinement means updating the named `field` in the YAML frontmatter block.
3. `KNOWLEDGE_CORE_DIR` and `REFINEMENTS_FILE` paths are resolved relative to the project root using `pathlib.Path`.
4. The frontend already renders a refinement list somewhere in `knowledge_core.js`; I'm adding buttons into that existing render function by name `renderRefinements`.
5. `api.js` already exists with a base `apiFetch` or `fetch` wrapper; I add two new exported functions at the bottom without touching existing ones.
6. Flask is the web framework; blueprints are already used for knowledge-core routes.

---

### Code

**`backend/routes/knowledge_core.py`** — modified (new endpoints added at bottom)

```python
import json
import tempfile
import os
from pathlib import Path
from flask import Blueprint, jsonify, request, current_app
import yaml

knowledge_core_bp = Blueprint("knowledge_core", __name__)

# ---------------------------------------------------------------------------
# Constants — paths are resolved at call time using current_app or a helper
# so nothing is hardcoded and tests can override the app config.
# ---------------------------------------------------------------------------

def _refinements_file(app) -> Path:
    return Path(app.config["DATA_DIR"]) / "refinements.json"


def _knowledge_core_dir(app) -> Path:
    return Path(app.config["KNOWLEDGE_CORE_DIR"])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _load_refinements(app) -> list[dict]:
    """Return the stored refinements list, or an empty list if none exist."""
    path = _refinements_file(app)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        # Surface a clear message rather than letting the caller crash.
        raise RuntimeError(f"Could not read refinements file: {exc}") from exc


def _save_refinements(app, refinements: list[dict]) -> None:
    """Write the refinements list atomically to avoid partial writes."""
    path = _refinements_file(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path_str = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(refinements, fh, indent=2)
        tmp_path.replace(path)  # atomic on POSIX; best-effort on Windows
    except OSError as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        finally:
            raise RuntimeError(f"Failed to save refinements: {exc}") from exc


def _find_refinement(refinements: list[dict], refinement_id: str) -> tuple[int, dict] | tuple[None, None]:
    """Return (index, refinement) for the matching id, or (None, None)."""
    for index, refinement in enumerate(refinements):
        if str(refinement.get("id")) == str(refinement_id):
            return index, refinement
    return None, None


def _apply_refinement_to_file(module_file: Path, field: str, suggested_value: str) -> None:
    """
    Update `field` in the YAML frontmatter of `module_file` to `suggested_value`.
    Writes atomically: temp file → rename.
    """
    if not module_file.exists():
        raise FileNotFoundError(f"Module file not found: {module_file}")

    raw = module_file.read_text(encoding="utf-8")

    # Split on the YAML frontmatter delimiters (---).
    # We expect the file to start with --- and have a closing ---.
    if not raw.startswith("---"):
        raise ValueError(f"Module file has no YAML frontmatter: {module_file}")

    parts = raw.split("---", 2)
    # parts[0] is empty string before first ---, parts[1] is YAML, parts[2] is body
    if len(parts) < 3:
        raise ValueError(f"Could not parse frontmatter in: {module_file}")

    frontmatter_text = parts[1]
    body = parts[2]

    try:
        frontmatter = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML frontmatter in {module_file}: {exc}") from exc

    frontmatter[field] = suggested_value
    updated_frontmatter = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True)
    updated_raw = f"---\n{updated_frontmatter}---{body}"

    tmp_fd, tmp_path_str = tempfile.mkstemp(dir=module_file.parent, suffix=".tmp")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            fh.write(updated_raw)
        tmp_path.replace(module_file)
    except OSError as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        finally:
            raise RuntimeError(f"Failed to write module file: {exc}") from exc


# ---------------------------------------------------------------------------
# Existing routes (unchanged — placeholders shown so the file stays coherent)
# ---------------------------------------------------------------------------

@knowledge_core_bp.route("/api/knowledge-core/refinements", methods=["GET"])
def get_refinements():
    """Return all stored refinements."""
    try:
        refinements = _load_refinements(current_app._get_current_object())
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "refinements": refinements})


# ---------------------------------------------------------------------------
# New: accept endpoint
# ---------------------------------------------------------------------------

@knowledge_core_bp.route("/api/knowledge-core/refinements/accept", methods=["POST"])
def accept_refinement():
    """
    Mark a refinement as accepted and apply its suggested value to the
    target knowledge-core module file.

    Request body: { "refinement_id": <str|int> }
    """
    body = request.get_json(silent=True) or {}
    refinement_id = body.get("refinement_id")

    if refinement_id is None:
        return jsonify({"ok": False, "error": "Missing required field: refinement_id"}), 400

    app = current_app._get_current_object()

    try:
        refinements = _load_refinements(app)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    index, refinement = _find_refinement(refinements, refinement_id)
    if refinement is None:
        return jsonify({"ok": False, "error": "Refinement not found"}), 404

    if refinement.get("status") in ("accepted", "rejected"):
        return jsonify({"ok": False, "error": "Refinement already actioned"}), 400

    # Locate the module file on disk.
    module_filename = refinement.get("module")
    if not module_filename:
        return jsonify({"ok": False, "error": "Refinement has no module field"}), 400

    module_file = _knowledge_core_dir(app) / module_filename
    field = refinement.get("field")
    suggested_value = refinement.get("suggested_value")

    if not field:
        return jsonify({"ok": False, "error": "Refinement has no field specified"}), 400

    try:
        _apply_refinement_to_file(module_file, field, suggested_value)
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except (ValueError, RuntimeError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    # Only update stored state after the file write succeeded.
    refinements[index]["status"] = "accepted"
    try:
        _save_refinements(app, refinements)
    except RuntimeError as exc:
        # File was already written; warn but don't fail the whole request.
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# New: reject endpoint
# ---------------------------------------------------------------------------

@knowledge_core_bp.route("/api/knowledge-core/refinements/reject", methods=["POST"])
def reject_refinement():
    """
    Mark a refinement as rejected. No knowledge-core file is modified.

    Request body: { "refinement_id": <str|int> }
    """
    body = request.get_json(silent=True) or {}
    refinement_id = body.get("refinement_id")

    if refinement_id is None:
        return jsonify({"ok": False, "error": "Missing required field: refinement_id"}), 400

    app = current_app._get_current_object()

    try:
        refinements = _load_refinements(app)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    index, refinement = _find_refinement(refinements, refinement_id)
    if refinement is None:
        return jsonify({"ok": False, "error": "Refinement not found"}), 404

    if refinement.get("status") in ("accepted", "rejected"):
        return jsonify({"ok": False, "error": "Refinement already actioned"}), 400

    refinements[index]["status"] = "rejected"
    try:
        _save_refinements(app, refinements)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True})
```

---

**`frontend/js/api.js`** — modified (two functions added at the bottom)

```javascript
// ---------------------------------------------------------------------------
// Existing code above this point is untouched.
// ---------------------------------------------------------------------------

/**
 * Call the accept endpoint for a single refinement.
 * Returns the parsed JSON response on success.
 * Throws a descriptive string on failure so callers can display it directly.
 *
 * @param {string|number} refinementId
 * @returns {Promise<{ok: boolean}>}
 */
async function acceptRefinement(refinementId) {
  let response;
  try {
    response = await fetch("/api/knowledge-core/refinements/accept", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refinement_id: refinementId }),
    });
  } catch (networkError) {
    throw `Network error while accepting refinement: ${networkError.message}`;
  }

  const data = await response.json().catch(() => ({}));

  if (!response.ok || !data.ok) {
    const reason = data.error || `HTTP ${response.status}`;
    throw `Failed to accept refinement: ${reason}`;
  }

  return data;
}

/**
 * Call the reject endpoint for a single refinement.
 * Returns the parsed JSON response on success.
 * Throws a descriptive string on failure so callers can display it directly.
 *
 * @param {string|number} refinementId
 * @returns {Promise<{ok: boolean}>}
 */
async function rejectRefinement(refinementId) {
  let response;
  try {
    response = await fetch("/api/knowledge-core/refinements/reject", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refinement_id: refinementId }),
    });
  } catch (networkError) {
    throw `Network error while rejecting refinement: ${networkError.message}`;
  }

  const data = await response.json().catch(() => ({}));

  if (!response.ok || !data.ok) {
    const reason = data.error || `HTTP ${response.status}`;
    throw `Failed to reject refinement: ${reason}`;
  }

  return data;
}
```

---

**`frontend/js/knowledge_core.js`** — modified (button injection added to render function)

```javascript
// ---------------------------------------------------------------------------
// Replace or extend your existing renderRefinements function with this one.
// The only additions are: the two buttons per card, their click handlers,
// and the status/error label elements. All existing card markup is preserved.
// ---------------------------------------------------------------------------

/**
 * Render the refinements list into the container element.
 * Each card gets Accept and Reject buttons that call the backend.
 *
 * @param {Array<Object>} refinements - Array of refinement objects from the API.
 * @param {HTMLElement} container - The DOM element to render into.
 */
function renderRefinements(refinements, container) {
  container.innerHTML = "";

  if (!refinements || refinements.length === 0) {
    const empty = document.createElement("p");
    empty.textContent = "No refinements available.";
    container.appendChild(empty);
    return;
  }

  refinements.forEach((refinement) => {
    const card = buildRefinementCard(refinement);
    container.appendChild(card);
  });
}

/**
 * Build a single refinement card element including Accept/Reject buttons.
 *
 * @param {Object} refinement
 * @returns {HTMLElement}
 */
function buildRefinementCard(refinement) {
  const card = document.createElement("div");
  card.classList.add("refinement-card");
  card.dataset.refinementId = refinement.id;

  // Summary of the refinement (existing display logic preserved).
  const summary = document.createElement("div");
  summary.classList.add("refinement-summary");
  summary.innerHTML = `
    <strong>Module:</strong> ${escapeHtml(refinement.module ?? "")}<br>
    <strong>Field:</strong> ${escapeHtml(refinement.field ?? "")}<br>
    <strong>Suggested value:</strong> ${escapeHtml(String(refinement.suggested_value ?? ""))}
  `;
  card.appendChild(summary);

  // Action row holds the two buttons and status/error labels.
  const actionRow = document.createElement("div");
  actionRow.classList.add("refinement-action-row");

  const acceptButton = document.createElement("button");
  acceptButton.textContent = "Accept";
  acceptButton.classList.add("btn-accept");

  const rejectButton = document.createElement("button");
  rejectButton.textContent = "Reject";
  rejectButton.classList.add("btn-reject");

  // Status label shown after a successful action.
  const statusLabel = document.createElement("span");
  statusLabel.classList.add("refinement-status");
  statusLabel.hidden = true;

  // Error label shown when the API call fails.
  const errorLabel = document.createElement("span");
  errorLabel.classList.add("refinement-error");
  errorLabel.hidden = true;

  // If the refinement was already actioned (page loaded mid-session), disable immediately.