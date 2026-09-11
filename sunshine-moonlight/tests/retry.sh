# Sourced by run.sh: installer/ownership integration in temporary HOME/PATH only.
new_case
run commands/install-host.sh
check 'managed retry initial install succeeds' test "$RC" -eq 0
dir="$(qd_retry_dir)"
check 'installed guard matches source bytes' cmp -s "$dir/check-tailnet.py" "$MODULE_DIR/service/check-tailnet.py"
check 'installed drop matches generated policy' cmp -s "$dir/quick-deploy-retry.conf" <(qd_retry_content "$QD_SUNSHINE_CONFIG_DIR")
check 'retry preserves vendor prestart sleep' contains "$CASE/loaded-retry" 'ExecStartPre=:/usr/bin/python3'
check 'retry does not replace native ExecStart' absent "$dir/quick-deploy-retry.conf" 'ExecStart='
check 'retry does not reset vendor prestart list' test "$(grep -c '^ExecStartPre=$' "$dir/quick-deploy-retry.conf")" -eq 0
check 'retry explicitly links graphical stop' contains "$dir/quick-deploy-retry.conf" 'PartOf=graphical-session.target'
helper_stat="$(stat -c '%i:%Y' "$dir/check-tailnet.py")"
drop_stat="$(stat -c '%i:%Y' "$dir/quick-deploy-retry.conf")"
: >"$CASE/log"
run commands/install-host.sh
check 'repeat retry install succeeds' test "$RC" -eq 0
check 'repeat install keeps helper inode/mtime' test "$helper_stat" = "$(stat -c '%i:%Y' "$dir/check-tailnet.py")"
check 'repeat install keeps drop inode/mtime' test "$drop_stat" = "$(stat -c '%i:%Y' "$dir/quick-deploy-retry.conf")"
check 'repeat install needs no daemon reload' absent "$CASE/log" 'daemon-reload'
check 'repeat install does not restart service' absent "$CASE/log" 'systemctl --user restart'
end_case

for residue in helper-only both-unloaded; do
 new_case; installed; write_conf
 dir="$(qd_retry_dir)"; mkdir -p "$dir"
 cp "$MODULE_DIR/service/check-tailnet.py" "$dir/check-tailnet.py"
 [ "$residue" != both-unloaded ] || qd_retry_content "$QD_SUNSHINE_CONFIG_DIR" >"$dir/quick-deploy-retry.conf"
 run commands/install-host.sh
 check "$residue interrupted retry installation converges" test "$RC" -eq 0
 check "$residue loads the managed drop-in" test -f "$CASE/loaded-retry"
 end_case
done

new_case; installed; write_conf
export MOCK_RESULT=start-limit-hit
run commands/install-host.sh
check 'old start-limit state is repaired by install' test "$RC" -eq 0
reset_line="$(grep -n 'reset-failed' "$CASE/log" | head -1 | cut -d: -f1)"
start_line="$(grep -n 'systemctl --user start ' "$CASE/log" | head -1 | cut -d: -f1)"
check 'old rate limit reset precedes start' test "$reset_line" -lt "$start_line"
end_case

for collision in helper drop extra-conf alias-conf directory-file directory-symlink helper-symlink drop-symlink; do
 new_case; installed; write_conf; active
 dir="$(qd_retry_dir)"; mkdir -p "$dir"
 case "$collision" in
 helper) printf '# foreign helper\n' >"$dir/check-tailnet.py";;
 drop) printf '# foreign policy\n[Service]\nRestart=always\n' >"$dir/quick-deploy-retry.conf";;
 extra-conf) printf '[Service]\nEnvironment=FOREIGN=yes\n' >"$dir/extra.conf";;
 alias-conf) mkdir -p "${dir%/*}/sunshine.service.d"; printf '# foreign alias\n' >"${dir%/*}/sunshine.service.d/extra.conf";;
 directory-file) rmdir "$dir"; printf 'foreign regular file\n' >"$dir";;
 directory-symlink) mv "$dir" "$CASE/foreign-dir"; printf 'keep\n' >"$CASE/foreign-dir/sentinel"; ln -s "$CASE/foreign-dir" "$dir";;
 helper-symlink) cp "$MODULE_DIR/service/check-tailnet.py" "$CASE/foreign-helper"; ln -s "$CASE/foreign-helper" "$dir/check-tailnet.py";;
 drop-symlink) qd_retry_content "$QD_SUNSHINE_CONFIG_DIR" >"$CASE/foreign-drop"; ln -s "$CASE/foreign-drop" "$dir/quick-deploy-retry.conf";;
 esac
 cp -a "$HOME/.config" "$CASE/config-before"
 run commands/install-host.sh
 check "$collision installation refused" test "$RC" -ne 0
 check "$collision refusal precedes package mutation" absent "$CASE/log" 'sudo '
 check "$collision installation preserves all config bytes" diff -qr "$CASE/config-before" "$HOME/.config"
 run commands/uninstall.sh --host-package --force-remove-preexisting-package
 check "$collision removal refused" test "$RC" -ne 0
 check "$collision removal preserves all config bytes" diff -qr "$CASE/config-before" "$HOME/.config"
 check "$collision refusal leaves service active" test -f "$CASE/active"
 check "$collision refusal precedes service disable" absent "$CASE/log" 'disable --now'
 end_case
done

for tampered in check-tailnet.py quick-deploy-retry.conf; do
 new_case; run commands/install-host.sh
 dir="$(qd_retry_dir)"
 printf '\n# edited by user\n' >>"$dir/$tampered"
 cp -a "$HOME/.config" "$CASE/config-before"; : >"$CASE/log"
 run commands/install-host.sh
 check "modified owned $tampered refused on repeat install" test "$RC" -ne 0
 run commands/uninstall.sh --destroy-host-state
 check "modified owned $tampered refused on state deletion" test "$RC" -ne 0
 check "modified owned $tampered bytes preserved" diff -qr "$CASE/config-before" "$HOME/.config"
 check "modified owned $tampered service left running" absent "$CASE/log" 'disable --now'
 end_case
done

for mismatch in restart delay limit partof extra-drop no-drop pre-missing pre-extra pre-ignored; do
 new_case; run commands/install-host.sh
 dir="$(qd_retry_dir)"
 case "$mismatch" in
 restart) export MOCK_RESTART=always;;
 delay) export MOCK_RESTART_USEC=0;;
 limit) export MOCK_LIMIT=500s;;
 partof) export MOCK_PARTOF=other.target;;
 extra-drop) export MOCK_DROPS="$dir/quick-deploy-retry.conf /tmp/foreign.conf";;
 no-drop) export MOCK_DROPS='';;
 pre-missing) export MOCK_PRE='{ path=/bin/sleep ; argv[]=/bin/sleep 5 ; ignore_errors=no ; }';;
 pre-extra|pre-ignored)
   pre="$(systemctl --user show "$QD_CANONICAL_UNIT" -p ExecStartPre --value)"
   if [ "$mismatch" = pre-extra ]; then export MOCK_PRE="$pre ; { path=/bin/true ; argv[]=/bin/true ; ignore_errors=no ; }";
   else export MOCK_PRE="${pre//ignore_errors=no/ignore_errors=yes}"; fi;;
 esac
 cp -a "$HOME/.config" "$CASE/config-before"; : >"$CASE/log"
 run commands/doctor.sh --host
 check "effective $mismatch diagnosed" test "$RC" -ne 0
 run commands/install-host.sh
 check "effective $mismatch never starts/restarts unchecked unit" absent "$CASE/log" 'systemctl --user restart'
 check "effective $mismatch preserves config and artifacts" diff -qr "$CASE/config-before" "$HOME/.config"
 run commands/uninstall.sh --destroy-host-state
 check "effective $mismatch removal refused" test "$RC" -ne 0
 check "effective $mismatch state retained" test -f "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
 end_case
done

for removal in package state combined absent-package stop-failure; do
 new_case; run commands/install-host.sh
 dir="$(qd_retry_dir)"
 printf 'unrelated script\n' >"$dir/user-notes.py"
 printf 'credentials retained\n' >"$QD_SUNSHINE_CONFIG_DIR/credentials.json"
 cp -a "$dir" "$CASE/retry-before"
 touch "$CASE/retrying"; rm -f "$CASE/active"; : >"$CASE/log"
 case "$removal" in
 absent-package) : >"$CASE/version"; export MOCK_UNIT_PRESENT=1;;
 stop-failure) export MOCK_STOP_FAIL=1;;
 esac
 case "$removal" in
 state) run commands/uninstall.sh --destroy-host-state;;
 combined) run commands/uninstall.sh --host-package --destroy-host-state;;
 *) run commands/uninstall.sh --host-package;;
 esac
 if [ "$removal" = stop-failure ]; then
   check 'failed stop prevents owned retry deletion' diff -qr "$CASE/retry-before" "$dir"
   check 'failed stop is an uninstall error' test "$RC" -ne 0
 else
   check "$removal removal succeeds" test "$RC" -eq 0
   check "$removal stop cancels modeled pending restart" test ! -f "$CASE/retrying"
   check "$removal removes owned helper" test ! -e "$dir/check-tailnet.py"
   check "$removal removes owned drop-in" test ! -e "$dir/quick-deploy-retry.conf"
   check "$removal preserves unrelated sibling bytes" contains "$dir/user-notes.py" 'unrelated script'
   stopline="$(grep -n 'disable --now' "$CASE/log" | head -1 | cut -d: -f1)"
   removeline="$(grep -n '^rm .*quick-deploy-retry.conf' "$CASE/log" | head -1 | cut -d: -f1)"
   reloadline="$(grep -n 'daemon-reload' "$CASE/log" | head -1 | cut -d: -f1)"
   check "$removal stop precedes artifact deletion" test "$stopline" -lt "$removeline"
   check "$removal artifact deletion precedes reload" test "$removeline" -lt "$reloadline"
   case "$removal" in
     package|absent-package) check "$removal keeps credentials byte-for-byte" contains "$QD_SUNSHINE_CONFIG_DIR/credentials.json" 'credentials retained';;
     *) check "$removal explicitly deletes selected state" test ! -e "$QD_SUNSHINE_CONFIG_DIR";;
   esac
 fi
 end_case
done

new_case; write_conf
mv "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" "$CASE/linked.conf"
ln -s "$CASE/linked.conf" "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
run commands/install-host.sh
check 'existing installer contract rejects config-file symlink' test "$RC" -ne 0
check 'config-file symlink refusal precedes package changes' absent "$CASE/log" 'sudo '
check 'config-file symlink target preserved' contains "$CASE/linked.conf" 'bind_address = 100.64.0.2'
end_case

new_case; installed; write_conf; active
export MOCK_RETRY_COLLISION=1
run commands/install-host.sh
dir="$(qd_retry_dir)"
check 'late helper-directory collision refuses install' test "$RC" -ne 0
check 'late collision preserves foreign sentinel' contains "$dir/check-tailnet.py/sentinel" 'foreign collision'
check 'late collision cannot receive a staged hardlink' test "$(find "$dir/check-tailnet.py" -mindepth 1 | wc -l)" -eq 1
check 'late collision leaves native active service alone' absent "$CASE/log" 'systemctl --user restart'
end_case

# These fixtures used to pass the line-wise guard despite different native values.
for shape in inline-comments embedded-list; do
 new_case; installed; write_conf
 conf="$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
 if [ "$shape" = inline-comments ]; then
   printf 'address_family = ipv4 # retain family explanation\nbind_address = 100.64.0.2 # retain address explanation\n' >"$conf"
 else
   printf 'unknown = [\naddress_family = ipv4\nbind_address = 100.64.0.2\n]\n' >"$conf"
   cp "$conf" "$CASE/list-before"
 fi
 printf 'custom = unchanged # retain bytes\n' >>"$conf"
 printf 'credentials retained\n' >"$QD_SUNSHINE_CONFIG_DIR/credentials.json"
 run commands/install-host.sh
 check "$shape installer converges" test "$RC" -eq 0
 check "$shape family readback is native exact" test "$(qd_conf_get "$conf" address_family)" = ipv4
 check "$shape bind readback is native exact" test "$(qd_conf_get "$conf" bind_address)" = 100.64.0.2
 check "$shape installed guard accepts emitted config" python3 "$(qd_retry_dir)/check-tailnet.py" "$(qd_host_config_dir)"
 check "$shape unrelated scalar retained" contains "$conf" 'custom = unchanged # retain bytes'
 check "$shape credentials unchanged" contains "$QD_SUNSHINE_CONFIG_DIR/credentials.json" 'credentials retained'
 if [ "$shape" = inline-comments ]; then
   check 'family inline comment becomes standalone' grep -qx '# retain family explanation' "$conf"
   check 'address inline comment becomes standalone' grep -qx '# retain address explanation' "$conf"
 else
   head -c "$(stat -c %s "$CASE/list-before")" "$conf" >"$CASE/list-after"
   check 'embedded pseudo-key list remains byte-identical' cmp -s "$CASE/list-before" "$CASE/list-after"
 fi
 cp "$conf" "$CASE/emitted"
 : >"$CASE/log"; run commands/doctor.sh --host
 check "$shape doctor accepts repaired binding" test "$RC" -eq 0
 run commands/install-host.sh
 check "$shape repeat install succeeds" test "$RC" -eq 0
 check "$shape repeat leaves config identical" cmp -s "$CASE/emitted" "$conf"
 check "$shape repeat does not restart" absent "$CASE/log" 'systemctl --user restart'
 end_case
 done

for malformed in duplicate missing-equals list-value unclosed-list; do
 new_case; installed; write_conf
 conf="$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
 case "$malformed" in
 duplicate) printf 'bind_address = 100.64.0.2\n' >>"$conf";;
 missing-equals) printf 'address_family # no equals\n' >>"$conf";;
 list-value) printf 'address_family=[ipv4]\n' >"$conf";;
 unclosed-list) printf 'unknown=[\n' >"$conf";;
 esac
 cp "$conf" "$CASE/before"
 run commands/install-host.sh
 check "$malformed rejected before install" test "$RC" -ne 0
 check "$malformed refusal preserves config" cmp -s "$CASE/before" "$conf"
 check "$malformed refusal precedes package mutation" absent "$CASE/log" 'sudo '
 check "$malformed refusal precedes reload" absent "$CASE/log" 'daemon-reload'
 end_case
 done

# Native systemd resolves parent links in DropInPaths, but leaves ExecStartPre argv literal.
for parent in HOME XDG; do
 for removal in package state; do
  new_case
  if [ "$parent" = HOME ]; then
    mv "$HOME" "$CASE/real-home"; ln -s "$CASE/real-home" "$HOME"
  else
    mkdir -p "$CASE/real-xdg"; ln -s "$CASE/real-xdg" "$CASE/config-link"
    export XDG_CONFIG_HOME="$CASE/config-link"
    export MOCK_MANAGER_ENV=$'DISPLAY=:1\nXDG_CONFIG_HOME='"$XDG_CONFIG_HOME"
  fi
  unset QD_SUNSHINE_CONFIG_DIR
  config="$(qd_host_config_dir)"
  run commands/install-host.sh
  check "$parent linked-parent $removal installation succeeds" test "$RC" -eq 0
  dir="$(qd_retry_dir)"
  check "$parent loaded path is native-canonical" test "$(cat "$CASE/loaded-retry-path")" = "$(readlink -f "$dir/quick-deploy-retry.conf")"
  check "$parent loaded path differs from lexical parent" test "$(cat "$CASE/loaded-retry-path")" != "$dir/quick-deploy-retry.conf"
  check "$parent guard argument retains literal linked parent" contains "$dir/quick-deploy-retry.conf" "\"$dir/check-tailnet.py\""
  check "$parent installed guard uses expected config" python3 "$dir/check-tailnet.py" "$config"
  printf 'unrelated sibling\n' >"$dir/user-notes.py"
  printf 'unrelated config sibling\n' >"${config%/*}/keep.txt"
  printf 'credentials retained\n' >"$config/credentials.json"
  run commands/doctor.sh --host
  check "$parent linked-parent $removal doctor succeeds" test "$RC" -eq 0
  helper_stat="$(stat -c '%i:%Y' "$dir/check-tailnet.py")"
  drop_stat="$(stat -c '%i:%Y' "$dir/quick-deploy-retry.conf")"
  cp "$config/sunshine.conf" "$CASE/emitted"; : >"$CASE/log"
  run commands/install-host.sh
  check "$parent linked-parent $removal repeat succeeds" test "$RC" -eq 0
  check "$parent repeat preserves helper inode/mtime" test "$helper_stat" = "$(stat -c '%i:%Y' "$dir/check-tailnet.py")"
  check "$parent repeat preserves drop inode/mtime" test "$drop_stat" = "$(stat -c '%i:%Y' "$dir/quick-deploy-retry.conf")"
  check "$parent repeat preserves config bytes" cmp -s "$CASE/emitted" "$config/sunshine.conf"
  check "$parent repeat avoids reload" absent "$CASE/log" 'daemon-reload'
  check "$parent repeat avoids restart" absent "$CASE/log" 'systemctl --user restart'
  pre="$(systemctl --user show "$QD_CANONICAL_UNIT" -p ExecStartPre --value)"
  real_dir="$(readlink -f "$dir")"
  export MOCK_PRE="${pre//"$dir"/"$real_dir"}"
  run commands/doctor.sh --host
  check "$parent canonicalizing literal ExecStartPre is rejected" test "$RC" -ne 0
  unset MOCK_PRE
  : >"$CASE/log"
  if [ "$removal" = package ]; then run commands/uninstall.sh --host-package;
  else run commands/uninstall.sh --destroy-host-state; fi
  check "$parent linked-parent $removal removal succeeds" test "$RC" -eq 0
  check "$parent removal deletes owned helper" test ! -e "$dir/check-tailnet.py"
  check "$parent removal deletes owned drop-in" test ! -e "$dir/quick-deploy-retry.conf"
  check "$parent removal preserves non-conf sibling" contains "$dir/user-notes.py" 'unrelated sibling'
  check "$parent removal preserves config sibling" contains "${config%/*}/keep.txt" 'unrelated config sibling'
  stopline="$(grep -n 'disable --now' "$CASE/log" | head -1 | cut -d: -f1)"
  removeline="$(grep -n '^rm .*quick-deploy-retry.conf' "$CASE/log" | head -1 | cut -d: -f1)"
  check "$parent stop precedes owned artifact deletion" test "$stopline" -lt "$removeline"
  if [ "$removal" = package ]; then
    check "$parent package removal keeps credentials" contains "$config/credentials.json" 'credentials retained'
  else check "$parent explicit state removal deletes selected state" test ! -e "$config"; fi
  end_case
 done
 done
