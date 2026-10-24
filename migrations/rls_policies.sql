-- Row-Level Security policies (OPT-IN, work package W3 evaluation).
--
-- Evaluation: RLS is a worthwhile SECOND barrier behind the
-- application level membership checks, because a bug in one query
-- then cannot cross workspaces. It is not enabled by default here:
-- the application's connection-per-operation pattern must first
-- set the settings below on every transaction, or every query is
-- denied. Enable only with a dedicated application role and a
-- connection layer that sets:
--   SET LOCAL app.workspace_id = '<workspace>';
--   SET LOCAL app.user_id = '<user>';
--
-- Activation: run this file after migration 0001, as the table
-- owner, once the application sets those values per transaction.

ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;

CREATE POLICY workspaces_by_membership ON workspaces
    USING (workspace_id = current_setting('app.workspace_id', true));
CREATE POLICY members_by_workspace ON workspace_members
    USING (workspace_id = current_setting('app.workspace_id', true));
CREATE POLICY invitations_by_workspace ON invitations
    USING (workspace_id = current_setting('app.workspace_id', true));
CREATE POLICY users_by_id ON users
    USING (user_id = current_setting('app.user_id', true));
