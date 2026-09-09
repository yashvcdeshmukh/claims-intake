# Tool comparison

I did Day 4 in Cursor only. The comparison is between its surfaces, on one
slice: HTTP tests, README, and the image.

**Worktrees** kept the four branches (`readme`, `docker`, `http-tests`,
`http-routes`) from sharing an index. **Ask / Plan** read those artifacts
against the brief without editing. That is how the merge leftover showed up:
`test_routes.py` and `test_notifications.py` both hit `POST /notifications`.
**Agent** then folded them into `test_routes.py`, added a sample `curl`,
committed, and pushed.

## Easy vs awkward

Worktrees made separate commits actually separate. They also lie if you look
at the wrong directory: the `http-routes` worktree stayed at `0f96dbd` with
both test files while the main checkout had the consolidation uncommitted.

Ask is cheap for "does this match the contract?" An agent with write access
starts writing. Ask cannot stage a delete, so the useful question and the
useful mutation are different modes.

Agent is the right tool once the decision is already made. It is the wrong
tool for a three-line `if`, and it will solve a problem you did not ask: it
offered a local venv on a lab whose README says you are in the container and
there is no install step.

## What I reach for

Worktrees when commits must not contaminate each other. Ask when the grade is
matching a document I did not write. Agent when the next verb is `commit` or
`push`.

If I only get one, I keep Ask and do the git myself. An agent that merges
four branches without reading the brief will keep both test files. That is
what happened here.
