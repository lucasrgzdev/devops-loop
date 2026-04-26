### Goal
Add Accept and Reject buttons to the knowledge-core dashboard so the user can approve or discard each suggested refinement, with accepted refinements applied to the relevant knowledge-core module file.

---

### Approach

1. **Understand the current refinement flow**
   - Read `backend/routes/knowledge_core.py` to find how refinements are currently fetched and stored.
   - Read `backend/services/processor_service.py` and `backend/services/ai_service.py` to understand how refinements are generated and what shape they have (field name, suggested value, module affected).
   - Read `backend/prompts/refinement.md` to confirm the schema of a refinement object returned by the AI.
   - Read `frontend/js/knowledge_core.js` and `frontend/knowledge_core.html` to see how refinements are currently rendered and what data is already in the DOM.

2. **Define the refinement data contract**
   - Assumption: each refinement has at minimum: `id` (or index), `module` (which knowledge-core file it targets), `field`, and `suggested_value`. State this assumption explicitly in a comment if the actual shape differs after reading the code.
   - If `id` does not exist on refinements, use a deterministic hash or index as identifier so the backend can look up which refinement to act on.

3. **Add a backend endpoint: `POST /api/knowledge-core/refinements/accept`**
   - In `backend/routes/knowledge_core.py`, add a new route.
   - Request body: `{ "refinement_id": <str or int> }`.
   - The handler must: load the stored refinements, find the matching one by id, locate the target module file under `/knowledge-core/`, read the file, apply the change (update the YAML frontmatter field or body field as appropriate), write atomically (write to a temp file, then rename — per project rule), mark the refinement as accepted in stored state, return `{ "ok": true }`.
   - If the refinement id is not found, return a clear 404 with a human-readable message.
   - If the file does not exist, return a clear 404 with a human-readable message.

4. **Add a backend endpoint: `POST /api/knowledge-core/refinements/reject`**
   - In the same route file, add a reject route.
   - Request body: `{ "refinement_id": <str or int> }`.
   - The handler must: load stored refinements, find the matching one, mark it as rejected (do not modify any file), return `{ "ok": true }`.
   - If the refinement id is not found, return a clear 404 with a human-readable message.

5. **Update frontend: render Accept/Reject buttons per refinement**
   - In `frontend/js/knowledge_core.js`, wherever refinements are rendered into the DOM, add two `<button>` elements per refinement card: "Accept" and "Reject".
   - Each button must carry the refinement id as a `data-` attribute.
   - Wire click handlers: on click, call the appropriate API endpoint with the refinement id, then — on success — visually update the card (disable both buttons, show a status label "Accepted" or "Rejected") so the user has clear feedback.
   - On API error, show a plain human-readable message near the card (do not use `alert()`).

6. **Update `frontend/knowledge_core.html` if needed**
   - Only add markup if the buttons cannot be injected purely from JS. Prefer injecting from JS to keep the template minimal.

7. **Update `frontend/css/knowledge_core.css`**
   - Add minimal styles for: `.btn-accept`, `.btn-reject`, `.refinement-status` (the post-action label). No elaborate design — legible and distinct is enough.

8. **Wire API calls in `frontend/js/api.js`**
   - Add two functions: `acceptRefinement(refinementId)` and `rejectRefinement(refinementId)`, both `async`, both returning the parsed JSON response or throwing a descriptive error string on failure.

---

### Inputs and outputs

**Backend — accept endpoint**
- Input: JSON body `{ "refinement_id": string | int }`
- Output: `{ "ok": true }` on success; `{ "ok": false, "error": "<human message>" }` with appropriate HTTP status on failure; the target knowledge-core markdown file is rewritten atomically with the accepted field value.

**Backend — reject endpoint**
- Input: JSON body `{ "refinement_id": string | int }`
- Output: `{ "ok": true }` on success; no file is modified.

**Frontend**
- Input: rendered refinement list already on page
- Output: each refinement card gains two buttons; clicking one calls the backend and updates the card's visual state in place.

---

### Edge cases to handle

- **Refinement id not found in stored state** — return 404 with message `"Refinement not found"`, do not crash.
- **Target module file does not exist on disk** — return 404 with message `"Module file not found: <path>"`, do not crash.
- **Refinement has already been accepted or rejected** — return 400 with message `"Refinement already actioned"` and leave both buttons disabled on the frontend after the first action.
- **Atomic write fails (e.g. disk full, permissions)** — catch the exception, return 500 with message `"Failed to write module file: <reason>"`, do not leave a partial file (temp file cleanup in a `finally` block).
- **Frontend API call fails** — display the error string next to the relevant card; do not remove the buttons so the user can retry.
- **No refinements exist** — the refinements section should render an empty state message (e.g. "No refinements available") rather than a broken or empty list; this is likely already handled but must remain intact after the change.

---

### Definition of done

- [ ] `POST /api/knowledge-core/refinements/accept` exists, accepts a refinement id, applies the change to the correct knowledge-core file atomically, and returns `{ "ok": true }`.
- [ ] `POST /api/knowledge-core/refinements/reject` exists, marks the refinement rejected without modifying any file, and returns `{ "ok": true }`.
- [ ] Both endpoints return a clear, human-readable error message (not a stack trace) for every error case listed above.
- [ ] The knowledge-core dashboard renders an Accept button and a Reject button for every refinement card.
- [ ] Clicking Accept calls the accept endpoint and — on success — disables both buttons and shows "Accepted" on the card.
- [ ] Clicking Reject calls the reject endpoint and — on success — disables both buttons and shows "Rejected" on the card.
- [ ] On API failure, a plain error message appears near the card without removing the buttons.
- [ ] No existing knowledge-core dashboard functionality is broken (refinement list still loads, module inspection still works).
- [ ] Button and status label styles exist in `knowledge_core.css` (`.btn-accept`, `.btn-reject`, `.refinement-status`).
- [ ] `acceptRefinement` and `rejectRefinement` functions exist in `api.js`.