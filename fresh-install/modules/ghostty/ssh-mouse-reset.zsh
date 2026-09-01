# SSH mouse-state cleanup for Ghostty's local zsh.
# Installed and maintained by fresh-install/modules/ghostty/install.sh.
#
# A remote TUI can leave DEC mouse-tracking modes enabled when an SSH channel
# dies before the TUI sends its cleanup sequence.  Ghostty cannot associate
# terminal modes with an SSH process, so the surviving shell clears the common
# modes when an interactive ssh command returns to its prompt.

# This file may be sourced from a shared zsh startup file, including on remote
# hosts. Keep it inert unless Ghostty supplied its child-shell resource path.
[[ -o interactive ]] || return 0
# Ghostty exports this resource path to its child shell. tmux may replace
# TERM_PROGRAM with `tmux`, so the resource path is the stable marker.
[[ -n ${GHOSTTY_RESOURCES_DIR:-} && -d ${GHOSTTY_RESOURCES_DIR:-} ]] || return 0
if (( ! ${+_quick_deploy_ghostty_ssh_mouse_loaded} )); then
    typeset -g _quick_deploy_ghostty_ssh_mouse_loaded=1
    typeset -gi _quick_deploy_ghostty_ssh_mouse_pending=0
fi

_quick_deploy_ghostty_ssh_is_assignment() {
    [[ "$1" =~ '^[[:alpha:]_][[:alnum:]_]*=' ]]
}

_quick_deploy_ghostty_ssh_preexec() {
    _quick_deploy_ghostty_ssh_mouse_pending=0
    local command="${1##[[:space:]]#}"
    local -a words
    setopt localoptions noglob
    words=("${(@z)command}")

    local i=1 word
    while (( i <= ${#words} )); do
        word="${words[i]}"

        case "$word" in
            ';'|'&&'|'||'|'|'|'&')
                return 0
                ;;
        esac

        if _quick_deploy_ghostty_ssh_is_assignment "$word"; then
            i=$((i + 1))
            continue
        fi

        case "$word" in
            command|builtin|noglob)
                i=$((i + 1))
                continue
                ;;
            env)
                i=$((i + 1))
                while (( i <= ${#words} )); do
                    word="${words[i]}"
                    case "$word" in
                        --|'')
                            i=$((i + 1))
                            break
                            ;;
                        -u|--unset|-C|--chdir)
                            i=$((i + 2))
                            ;;
                        -*|[[:alpha:]_][[:alnum:]_]*=*)
                            i=$((i + 1))
                            ;;
                        *)
                            break
                            ;;
                    esac
                done
                continue
                ;;
            sudo)
                i=$((i + 1))
                while (( i <= ${#words} )); do
                    word="${words[i]}"
                    case "$word" in
                        --|'')
                            i=$((i + 1))
                            break
                            ;;
                        -u|-g|-C|-R|-r|-t|-p|-h|--user|--group|--chdir|--role|--type|--prompt|--host|--close-from)
                            i=$((i + 2))
                            ;;
                        -*|[[:alpha:]_][[:alnum:]_]*=*)
                            i=$((i + 1))
                            ;;
                        *)
                            break
                            ;;
                    esac
                done
                continue
                ;;
        esac

        if [[ "${word##*/}" == ssh ]]; then
            _quick_deploy_ghostty_ssh_mouse_pending=1
        fi
        return 0
    done

    return 0
}

_quick_deploy_ghostty_ssh_precmd() {
    local _quick_deploy_status=$?
    if (( _quick_deploy_ghostty_ssh_mouse_pending )); then
        _quick_deploy_ghostty_ssh_mouse_pending=0

        # tmux parses pane output and continues to own its outer mouse mode.
        # GNU screen's ownership is not assumed, so skip it conservatively.
        if [[ -z ${STY:-} && -t 1 && ${TERM:-} != dumb ]]; then
            # 1000/1001/1002/1003 cover click, highlight, button-event, and
            # any-event tracking. 1005/1006/1015/1016 cover common encodings.
            # Do not reset bracketed paste, focus reporting, or keyboard modes.
            printf '\033[?1000l\033[?1001l\033[?1002l\033[?1003l\033[?1005l\033[?1006l\033[?1015l\033[?1016l' || :
        fi
    fi
    return $_quick_deploy_status
}

# The hook must be loaded after the shell's normal setup; it only registers
# functions and never changes the active terminal until an ssh command returns.
# add-zsh-hook de-duplicates entries, so re-sourcing .zshrc can restore the
# registration if another startup file rebuilt the hook arrays.
autoload -Uz add-zsh-hook
add-zsh-hook preexec _quick_deploy_ghostty_ssh_preexec
add-zsh-hook precmd _quick_deploy_ghostty_ssh_precmd
