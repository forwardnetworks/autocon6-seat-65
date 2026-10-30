# Beta activity log: after each terminal command, report the command line and its exit code to the workshop
# service (secrets masked first). Sourced from ~/.bashrc and ~/.zshrc by bootstrap.sh. Does nothing unless this
# seat's TELEMETRY_KEY secret and AC6_TELEMETRY_URL are set. Runs in the background and never prints.

if [ -n "${TELEMETRY_KEY:-}" ] && [ -n "${AC6_TELEMETRY_URL:-}" ] && [ -n "${AC6_ROOT:-}" ] && command -v python3 >/dev/null 2>&1; then
  __ac6_send() {
    # $1 exit code, $2 command line
    [ -n "$2" ] || return 0
    ( python3 "$AC6_ROOT/src/workshop/telemetry.py" cmd "$1" "$PWD" "$2" >/dev/null 2>&1 & )
  }

  if [ -n "${BASH_VERSION:-}" ]; then
    __ac6_last_hist=""
    __ac6_precmd() {
      local code=$?
      local entry num line
      entry=$(HISTTIMEFORMAT= history 1)
      if [[ $entry =~ ^[[:space:]]*([0-9]+)[[:space:]]+(.*)$ ]]; then
        num=${BASH_REMATCH[1]}
        line=${BASH_REMATCH[2]}
        if [ "$num" != "$__ac6_last_hist" ]; then
          __ac6_last_hist=$num
          __ac6_send "$code" "$line"
        fi
      fi
      return $code
    }
    case ";${PROMPT_COMMAND:-};" in
      *";__ac6_precmd;"*) ;;
      *) PROMPT_COMMAND="__ac6_precmd${PROMPT_COMMAND:+;$PROMPT_COMMAND}" ;;
    esac
  elif [ -n "${ZSH_VERSION:-}" ]; then
    __ac6_cmd=""
    __ac6_preexec() { __ac6_cmd="$1"; }
    __ac6_precmd() {
      local code=$?
      __ac6_send "$code" "$__ac6_cmd"
      __ac6_cmd=""
      return $code
    }
    autoload -Uz add-zsh-hook 2>/dev/null && add-zsh-hook preexec __ac6_preexec && add-zsh-hook precmd __ac6_precmd
  fi
fi
