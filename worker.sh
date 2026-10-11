#!/usr/bin/env bash
set -u
umask 077
cd /opt/data/workspace || exit 3
for cmd in hermes timeout; do
 command -v "$cmd" >/dev/null 2>&1 || { echo "Missing: $cmd"; exit 3; }
done
hermes --version
PROMPT='Use write_file to create /opt/data/workspace/agent-proof.txt containing exactly the ASCII text container-agent-ok followed by exactly one newline (LF). Then use read_file to read that same file. Do not use a shell, code execution, or another method to create the file. Report whether the read-back matches.'
timeout --kill-after=5s 240s hermes -z "$PROMPT" --provider lmstudio \
 -m "$TEST_MODEL" -t file --in /opt/data/workspace --usage-file /opt/data/usage.json
AGENT_EXIT=$?
echo "$AGENT_EXIT" > /opt/data/agent_exit.txt
echo "AGENT_EXIT:$AGENT_EXIT"
timeout --kill-after=5s 30s hermes sessions export --format jsonl \
 --cwd /opt/data/workspace --redact --yes /opt/data/session_trace.jsonl
EXPORT_EXIT=$?
echo "$EXPORT_EXIT" > /opt/data/export_exit.txt
echo "EXPORT_EXIT:$EXPORT_EXIT"
# The host grader, not this process, determines task success.
[[ $EXPORT_EXIT -eq 0 ]] || exit 3
exit 0
