#!/usr/bin/env bash
# ==============================================================================
# tmux-agent-switcher.tmux — Official Tmux Plugin Manager (TPM) entrypoint
#
# Usage in ~/.tmux.conf:
#   set -g @plugin 'xzy9565/tmux-agent-switcher'
#
# Custom options:
#   set -g @agent-switcher-bind 'Space'         # Default key to trigger popup
#   set -g @agent-switcher-width '88%'          # Popup width
#   set -g @agent-switcher-height '82%'         # Popup height
#   set -g @agent-switcher-preview-width '58%'  # Preview split width
#   set -g @agent-switcher-mark-input 'on'      # Highlight input-needed pane with yellow border (on/off)
#   set -g @agent-switcher-notify 'none'        # Notifications: none, bell, desktop, passthrough, all
#   set -g @agent-switcher-hook 'on'            # Auto-register title & clearance hooks (on/off)
# ==============================================================================

CURRENT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BIN="$CURRENT_DIR/bin/tmux-agent-switcher"

if [ ! -f "$BIN" ]; then
    BIN="$(which tmux-agent-switcher 2>/dev/null)"
fi

get_tmux_option() {
    local option="$1"
    local default_value="$2"
    local option_value
    option_value="$(tmux show-option -gqv "$option")"
    if [ -z "$option_value" ]; then
        echo "$default_value"
    else
        echo "$option_value"
    fi
}

main() {
    local key_binding
    local popup_width
    local popup_height
    local preview_width

    key_binding="$(get_tmux_option "@agent-switcher-bind" "Space")"
    popup_width="$(get_tmux_option "@agent-switcher-width" "88%")"
    popup_height="$(get_tmux_option "@agent-switcher-height" "82%")"
    preview_width="$(get_tmux_option "@agent-switcher-preview-width" "58%")"

    # Unbind default key to prevent conflicts
    tmux unbind-key "$key_binding" 2>/dev/null || true

    # Register display-popup binding
    tmux bind-key "$key_binding" display-popup \
        -w "$popup_width" \
        -h "$popup_height" \
        -E "$BIN run --preview-width '$preview_width'"

    # Automatic Tmux hooks for agent status tracking and alert clearance
    local hook_enabled
    hook_enabled="$(get_tmux_option "@agent-switcher-hook" "on")"

    if [ "$hook_enabled" = "on" ]; then
        # A pane's alert mark clears when it gains focus, however you get there: select-pane, last-pane,
        # the popup, a window/session switch, or back from another app. Needs focus-events, as does focus suppression.
        tmux set -s focus-events on
        tmux set-hook -g pane-focus-in "run-shell '$BIN clear #{pane_id} >/dev/null 2>&1 || true'"
        # A turn that ended while you watched alerts once you leave without replying. The guard skips the spawn otherwise.
        tmux set-hook -g pane-focus-out "if -F '#{@pane-alert-pending}' { run-shell -b '$BIN focus-out #{pane_id} >/dev/null 2>&1 || true' }"
    fi
}

main
