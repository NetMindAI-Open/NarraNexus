#!/bin/bash
# Start all services (tmux multi-window)
#
# Usage:
#   bash start/all.sh          # Start all services
#   bash start/all.sh --no-fe  # Start backend only, skip frontend
#
# Management:
#   tmux attach -t xyz-dev     # Enter tmux
#   Ctrl-b + n / p             # Switch windows
#   Switch to [control] and press q  # Stop all

SESSION=xyz-dev
DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ============================================================================
# Send command to a tmux window with retry
#   If the window's shell hasn't started the command after 3 seconds, resend it.
#   Detection: check if the pane is still running plain "bash"/"zsh" (idle shell).
# ============================================================================
tmux_send_with_retry() {
    local window="$1"
    local cmd="$2"
    local max_retries=3

    tmux send-keys -t "$SESSION:$window" "$cmd" C-m

    for i in $(seq 1 $max_retries); do
        sleep 3
        # Get the current command running in the pane
        local pane_cmd
        pane_cmd=$(tmux display-message -t "$SESSION:$window" -p '#{pane_current_command}' 2>/dev/null)
        # If pane is still at idle shell (bash/zsh/sh), the command didn't start
        if [ "$pane_cmd" = "bash" ] || [ "$pane_cmd" = "zsh" ] || [ "$pane_cmd" = "sh" ]; then
            tmux send-keys -t "$SESSION:$window" "$cmd" C-m
        else
            # Command is running
            return 0
        fi
    done
}

# Kill existing session if present
tmux has-session -t $SESSION 2>/dev/null && tmux kill-session -t $SESSION

# Window 0: Control Panel
tmux new-session -d -s $SESSION -n control -c "$DIR"
tmux_send_with_retry control "bash start/control.sh"

# Window 1: Frontend (optionally skip)
if [ "$1" != "--no-fe" ]; then
    tmux new-window -t $SESSION -n frontend -c "$DIR"
    tmux_send_with_retry frontend "bash start/frontend.sh"
fi

# Window 2: FastAPI Backend
tmux new-window -t $SESSION -n backend -c "$DIR"
tmux_send_with_retry backend "bash start/backend.sh"

# Window 3: Job Trigger
tmux new-window -t $SESSION -n job-trigger -c "$DIR"
tmux_send_with_retry job-trigger "bash start/job-trigger.sh"

# Window 4: ModulePoller
tmux new-window -t $SESSION -n poller -c "$DIR"
tmux_send_with_retry poller "bash start/poller.sh"

# Window 5: MCP Server
tmux new-window -t $SESSION -n mcp -c "$DIR"
tmux_send_with_retry mcp "bash start/mcp.sh"

# Window 6: Telegram Trigger (optional, requires TELEGRAM_BOT_TOKEN or DB binding)
if [ "$1" != "--no-telegram" ]; then
    tmux new-window -t $SESSION -n telegram -c "$DIR"
    tmux_send_with_retry telegram "bash start/telegram-trigger.sh"
fi

# Switch to control window and attach
tmux select-window -t $SESSION:control
tmux attach -t $SESSION
