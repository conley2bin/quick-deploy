#!/bin/bash
# 仅加载安装器函数，在隔离 HOME 中验证两块收敛行为：
#   1. Fcitx5 XDG autostart（原有全部场景）
#   2. chttrans 简繁转换治理：插件禁用 + conf/chttrans.conf 防御性清理 + 精确回读校验
# 不安装软件包、不启动真实 fcitx5、不触碰真实用户配置（全部在 mktemp HOME 内）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="$SCRIPT_DIR/../install.sh"
WORK_DIR="$(mktemp -d)"
FUNCTIONS="$WORK_DIR/functions.sh"
trap 'rm -rf "$WORK_DIR"' EXIT

# 安装器的参数解析之后是有副作用的安装主流程；测试只需函数定义和常量。
sed '/^parse_args "\$@"$/,$d' "$INSTALLER" > "$FUNCTIONS"

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

assert_file_contains() {
    local file="$1" expected="$2"
    grep -qxF "$expected" "$file" || fail "$file 缺少: $expected"
}

assert_file_not_contains() {
    local file="$1" unexpected="$2"
    if grep -qxF "$unexpected" "$file"; then
        fail "$file 不应再包含: $unexpected"
    fi
}

count_lines() {
    local file="$1" pattern="$2"
    grep -cE "$pattern" "$file" || true
}

# 与 fcitx5 readFromIni + vector 反序列化同语义地加载 [Behavior/DisabledAddons]
# 的最终向量: 重复节合并到同一节点、同索引后写覆盖先写、按 0,1,2… 连续读、
# 遇索引缺口即止。断言禁用列表一律用这个，不能只 grep 文本。
load_disabled_vector() {
    local file="$1"
    awk '
        BEGIN { maxidx = -1 }
        {
            t = $0
            sub(/\r$/, "", t)
            sub(/^[[:space:]]+/, "", t)
            sub(/[[:space:]]+$/, "", t)
            if (substr(t, 1, 1) == "[" && substr(t, length(t), 1) == "]") {
                in_da = (t == "[Behavior/DisabledAddons]")
            } else if (in_da && t ~ /^[0-9]+=/) {
                idx = t; sub(/=.*/, "", idx)
                val = t; sub(/^[^=]*=/, "", val)
                eff[idx + 0] = val
                if (idx + 0 > maxidx) maxidx = idx + 0
            }
        }
        END {
            for (i = 0; i <= maxidx; i++) {
                if (!(i in eff)) break
                print eff[i]
            }
        }
    ' "$file"
}

assert_disabled_vector() {
    local file="$1" actual expected
    shift
    expected="$(printf '%s\n' "$@")"
    actual="$(load_disabled_vector "$file")"
    if [ "$actual" != "$expected" ]; then
        fail "$file 的 DisabledAddons 加载向量为 [$actual]，期望 [$expected]"
    fi
}

# 在指定 HOME 里依次执行安装器的指定函数。
# 只跑纯文件操作函数；HOME 之外的路径不会被触碰。
run_helper() {
    local home="$1"
    shift
    HOME="$home" bash -c '
        set -e
        source "$1"
        shift
        for fn in "$@"; do
            "$fn"
        done
    ' _ "$FUNCTIONS" "$@"
}

CHTTRANS_CONF_RELPATH=".config/fcitx5/conf/chttrans.conf"
FCITX5_CONFIG_RELPATH=".config/fcitx5/config"

new_home() {
    local home="$WORK_DIR/$1"
    mkdir -p "$home"
    echo "$home"
}

seed_traditional_state() {
    # 模拟装过且切到过繁体的机器：EnabledIM 含 pinyin、热键 Ctrl+Shift+F，
    # 夹杂注释、未知节键与 OpenCC 引擎配置。EnabledIM 的非空持久化形态是
    # [EnabledIM] 数字列表（fcitx5 vector 选项的真实落盘形态）。
    local home="$1"
    mkdir -p "$home/.config/fcitx5/conf"
    cat > "$home/$CHTTRANS_CONF_RELPATH" << 'EOF'
# 转换引擎
Engine=OpenCC
# 启用的输入法
EnabledIM=pinyin
# 简转繁的 OpenCC 配置
OpenCCS2TProfile=
# 繁转简的 OpenCC 配置
OpenCCT2SProfile=

[EnabledIM]
0=pinyin
1=shuangpin

[Hotkey]
0=Control+Shift+F
1=Control+Shift+M

[Personal]
Keep=Yes
EOF
    cat > "$home/$FCITX5_CONFIG_RELPATH" << 'EOF'
[Hotkey]
AltTriggerKeys=

[Hotkey/EnumerateForwardKeys]
0=Shift+Shift_L

[Behavior]
# 每页候选词数
DefaultPageSize=5
ActiveByDefault=True
# Force Disabled Addons
DisabledAddons=
EOF
}

# ---------------------------------------------------------------------------
# A. Fcitx5 XDG autostart（原有测试，全部保留）
# ---------------------------------------------------------------------------
HOME_DIR="$(new_home home-autostart)"
AUTOSTART="$HOME_DIR/.config/autostart/fcitx5.desktop"

# 空 HOME 首次创建受管、启用的 XDG autostart 文件。
run_helper "$HOME_DIR" ensure_fcitx5_autostart verify_fcitx5_autostart >/dev/null
[ -f "$AUTOSTART" ] || fail "首次运行未创建 autostart 文件"
for line in \
    'Type=Application' \
    'Exec=/usr/bin/fcitx5 -d' \
    'Hidden=false' \
    'X-GNOME-Autostart-enabled=true' \
    'X-Quick-Deploy-Managed=true'; do
    assert_file_contains "$AUTOSTART" "$line"
done

# 再次运行字节级不变，且不会产生备份。
first_digest="$(sha256sum "$AUTOSTART" | awk '{print $1}')"
run_helper "$HOME_DIR" ensure_fcitx5_autostart verify_fcitx5_autostart >/dev/null
second_digest="$(sha256sum "$AUTOSTART" | awk '{print $1}')"
[ "$first_digest" = "$second_digest" ] || fail "相同配置的重跑修改了 autostart 文件"
if compgen -G "$AUTOSTART.bak.*" >/dev/null; then
    fail "相同配置的重跑创建了备份"
fi

# 精确匹配的旧版 quick-deploy 文件会备份并迁移到受管定义。
rm -f "$AUTOSTART"
cat > "$AUTOSTART" << 'EOF'
[Desktop Entry]
Name=Fcitx 5
GenericName=Input Method
Comment=Start Input Method
Exec=fcitx5
Icon=fcitx
Terminal=false
Type=Application
Categories=System;Utility;
StartupNotify=false
X-GNOME-Autostart-Phase=Applications
X-GNOME-AutoRestart=false
X-GNOME-Autostart-Notify=false
X-KDE-autostart-after=panel
EOF
run_helper "$HOME_DIR" ensure_fcitx5_autostart verify_fcitx5_autostart >/dev/null
assert_file_contains "$AUTOSTART" 'Exec=/usr/bin/fcitx5 -d'
compgen -G "$AUTOSTART.bak.*" >/dev/null || fail "旧版文件迁移前未备份"

# 已受管但被手动禁用的文件会被恢复为启用定义。
sed -i 's/^X-GNOME-Autostart-enabled=true$/X-GNOME-Autostart-enabled=false/' "$AUTOSTART"
run_helper "$HOME_DIR" ensure_fcitx5_autostart verify_fcitx5_autostart >/dev/null
assert_file_contains "$AUTOSTART" 'X-GNOME-Autostart-enabled=true'

# 未知用户文件必须保留，不能被安装器静默覆盖。
printf '%s\n' '[Desktop Entry]' 'Exec=custom-fcitx-wrapper' > "$AUTOSTART"
if run_helper "$HOME_DIR" ensure_fcitx5_autostart verify_fcitx5_autostart >/dev/null 2>&1; then
    fail "未知用户自启动文件被错误接受"
fi
assert_file_contains "$AUTOSTART" 'Exec=custom-fcitx-wrapper'

# ---------------------------------------------------------------------------
# B. chttrans 治理：空 HOME 从零创建
# ---------------------------------------------------------------------------
HOME_FRESH="$(new_home home-chttrans-fresh)"
run_helper "$HOME_FRESH" disable_chttrans_addon neutralize_chttrans_conf >/dev/null
run_helper "$HOME_FRESH" verify_chttrans_neutralized >/dev/null
[ -f "$HOME_FRESH/$CHTTRANS_CONF_RELPATH" ] || fail "空 HOME 未创建 chttrans.conf"
[ "$(count_lines "$HOME_FRESH/$CHTTRANS_CONF_RELPATH" '^EnabledIM=$')" = "1" ] \
    || fail "空 HOME 的 chttrans.conf 缺少唯一的顶层 EnabledIM="
[ "$(count_lines "$HOME_FRESH/$CHTTRANS_CONF_RELPATH" '^Hotkey=$')" = "1" ] \
    || fail "空 HOME 的 chttrans.conf 缺少唯一的顶层 Hotkey= 空键表标记"
[ "$(count_lines "$HOME_FRESH/$CHTTRANS_CONF_RELPATH" '^\[Hotkey\]$')" = "0" ] \
    || fail "空 HOME 的 chttrans.conf 不应存在 [Hotkey] 节"
assert_file_contains "$HOME_FRESH/$FCITX5_CONFIG_RELPATH" '[Behavior/DisabledAddons]'
assert_file_contains "$HOME_FRESH/$FCITX5_CONFIG_RELPATH" '0=chttrans'
if find "$HOME_FRESH/.config/fcitx5" -name '*.bak.*' | grep -q .; then
    fail "空 HOME 无中生有的内容不应产生备份"
fi

# ---------------------------------------------------------------------------
# C. 从 EnabledIM 含 pinyin + Ctrl+Shift+F 的旧状态迁移
# ---------------------------------------------------------------------------
HOME_MIGRATE="$(new_home home-chttrans-migrate)"
seed_traditional_state "$HOME_MIGRATE"
run_helper "$HOME_MIGRATE" disable_chttrans_addon neutralize_chttrans_conf >/dev/null
run_helper "$HOME_MIGRATE" verify_chttrans_neutralized >/dev/null

MIGRATED_CONF="$HOME_MIGRATE/$CHTTRANS_CONF_RELPATH"
# 无关字段/注释/未知节键全部保留
for line in \
    'Engine=OpenCC' \
    'OpenCCS2TProfile=' \
    'OpenCCT2SProfile=' \
    '# 转换引擎' \
    '[Personal]' \
    'Keep=Yes'; do
    assert_file_contains "$MIGRATED_CONF" "$line"
done
# 繁体状态与简繁快捷键全部清除
for line in \
    'EnabledIM=pinyin' \
    '0=pinyin' \
    '1=shuangpin' \
    '0=Control+Shift+F' \
    '1=Control+Shift+M' \
    '[EnabledIM]'; do
    assert_file_not_contains "$MIGRATED_CONF" "$line"
done
assert_file_contains "$MIGRATED_CONF" 'EnabledIM='
# config: Behavior 其它键保留；空列表的 DisabledAddons= 叶子行被规范为目标子节
MIGRATED_CONFIG="$HOME_MIGRATE/$FCITX5_CONFIG_RELPATH"
assert_file_contains "$MIGRATED_CONFIG" 'DefaultPageSize=5'
assert_file_contains "$MIGRATED_CONFIG" 'ActiveByDefault=True'
assert_file_contains "$MIGRATED_CONFIG" '[Behavior/DisabledAddons]'
assert_file_contains "$MIGRATED_CONFIG" '0=chttrans'
assert_file_not_contains "$MIGRATED_CONFIG" 'DisabledAddons='
assert_file_not_contains "$MIGRATED_CONFIG" '[DisabledAddons]'

# ---------------------------------------------------------------------------
# D. 重复目标节/键收敛为唯一
# ---------------------------------------------------------------------------
HOME_DUP="$(new_home home-chttrans-dup)"
mkdir -p "$HOME_DUP/.config/fcitx5/conf"
cat > "$HOME_DUP/$CHTTRANS_CONF_RELPATH" << 'EOF'
EnabledIM=old1
EnabledIM=old2

[Hotkey]
0=Control+Shift+F

[Personal]
Keep=Yes

[Hotkey]
0=Control+Shift+M
EOF
cat > "$HOME_DUP/$FCITX5_CONFIG_RELPATH" << 'EOF'
[Behavior]
ActiveByDefault=True
DisabledAddons=

[Behavior/DisabledAddons]
0=someotheraddon
EOF
run_helper "$HOME_DUP" disable_chttrans_addon neutralize_chttrans_conf >/dev/null
run_helper "$HOME_DUP" verify_chttrans_neutralized >/dev/null
DUP_CONF="$HOME_DUP/$CHTTRANS_CONF_RELPATH"
[ "$(count_lines "$DUP_CONF" '^EnabledIM=')" = "1" ] || fail "顶层 EnabledIM 未去重"
[ "$(count_lines "$DUP_CONF" '^Hotkey=$')" = "1" ] || fail "顶层 Hotkey= 空标记未去重"
[ "$(count_lines "$DUP_CONF" '^\[Hotkey\]$')" = "0" ] || fail "[Hotkey] 节未被消除并收敛为叶子"
[ "$(count_lines "$DUP_CONF" 'Keep=Yes')" = "1" ] || fail "未知节内容被误删"
assert_file_contains "$HOME_DUP/$FCITX5_CONFIG_RELPATH" '0=someotheraddon'
assert_file_contains "$HOME_DUP/$FCITX5_CONFIG_RELPATH" '1=chttrans'
assert_file_not_contains "$HOME_DUP/$FCITX5_CONFIG_RELPATH" 'DisabledAddons='
[ "$(grep -c '=chttrans$' "$HOME_DUP/$FCITX5_CONFIG_RELPATH" || true)" = "1" ] \
    || fail "config 中 chttrans 出现了多次"

# ---------------------------------------------------------------------------
# E. 幂等：重跑字节级不变、不新增备份
# ---------------------------------------------------------------------------
IDEM_CONF_BEFORE="$(sha256sum "$HOME_MIGRATE/$CHTTRANS_CONF_RELPATH" | awk '{print $1}')"
IDEM_CONFIG_BEFORE="$(sha256sum "$HOME_MIGRATE/$FCITX5_CONFIG_RELPATH" | awk '{print $1}')"
IDEM_BAKS_BEFORE="$(find "$HOME_MIGRATE/.config/fcitx5" -name '*.bak.*' | wc -l)"
run_helper "$HOME_MIGRATE" disable_chttrans_addon neutralize_chttrans_conf verify_chttrans_neutralized >/dev/null
[ "$(sha256sum "$HOME_MIGRATE/$CHTTRANS_CONF_RELPATH" | awk '{print $1}')" = "$IDEM_CONF_BEFORE" ] \
    || fail "已收敛的 chttrans.conf 被重跑改动"
[ "$(sha256sum "$HOME_MIGRATE/$FCITX5_CONFIG_RELPATH" | awk '{print $1}')" = "$IDEM_CONFIG_BEFORE" ] \
    || fail "已收敛的 config 被重跑改动"
[ "$(find "$HOME_MIGRATE/.config/fcitx5" -name '*.bak.*' | wc -l)" = "$IDEM_BAKS_BEFORE" ] \
    || fail "已收敛状态的重跑创建了新备份"

# ---------------------------------------------------------------------------
# F. 变化前先备份，备份内容等于原文件
# ---------------------------------------------------------------------------
HOME_BACKUP="$(new_home home-chttrans-backup)"
seed_traditional_state "$HOME_BACKUP"
cp "$HOME_BACKUP/$CHTTRANS_CONF_RELPATH" "$WORK_DIR/conf-before"
cp "$HOME_BACKUP/$FCITX5_CONFIG_RELPATH" "$WORK_DIR/config-before"
run_helper "$HOME_BACKUP" disable_chttrans_addon neutralize_chttrans_conf >/dev/null
BAK_CONF="$(compgen -G "$HOME_BACKUP/$CHTTRANS_CONF_RELPATH.bak.*" | head -n 1 || true)"
BAK_CONFIG="$(compgen -G "$HOME_BACKUP/$FCITX5_CONFIG_RELPATH.bak.*" | head -n 1 || true)"
[ -n "$BAK_CONF" ] || fail "chttrans.conf 变更前未备份"
[ -n "$BAK_CONFIG" ] || fail "config 变更前未备份"
cmp -s "$BAK_CONF" "$WORK_DIR/conf-before" || fail "chttrans.conf 备份内容与原文件不一致"
cmp -s "$BAK_CONFIG" "$WORK_DIR/config-before" || fail "config 备份内容与原文件不一致"

# ---------------------------------------------------------------------------
# G. 校验必须拒绝漂移（三项各自独立触发失败）
# ---------------------------------------------------------------------------
HOME_DRIFT="$(new_home home-chttrans-drift)"
seed_traditional_state "$HOME_DRIFT"
run_helper "$HOME_DRIFT" disable_chttrans_addon neutralize_chttrans_conf >/dev/null
run_helper "$HOME_DRIFT" verify_chttrans_neutralized >/dev/null

# G1: 简繁热键以两种形态回归都必须被拒绝: 节内数字项、顶层叶子被赋值
sed -i 's/^Hotkey=$/[Hotkey]\n0=Control+Shift+F\nHotkey=/' "$HOME_DRIFT/$CHTTRANS_CONF_RELPATH"
if run_helper "$HOME_DRIFT" verify_chttrans_neutralized >/dev/null 2>&1; then
    fail "校验未拒绝 [Hotkey] 节里重新出现的 Ctrl+Shift+F"
fi
sed -i '/^0=Control+Shift+F$/d; /^\[Hotkey\]$/d' "$HOME_DRIFT/$CHTTRANS_CONF_RELPATH"
sed -i 's/^Hotkey=$/Hotkey=Control+Shift+F/' "$HOME_DRIFT/$CHTTRANS_CONF_RELPATH"
if run_helper "$HOME_DRIFT" verify_chttrans_neutralized >/dev/null 2>&1; then
    fail "校验未拒绝顶层 Hotkey= 叶子被重新赋值"
fi
sed -i 's/^Hotkey=Control+Shift+F$/Hotkey=/' "$HOME_DRIFT/$CHTTRANS_CONF_RELPATH"
run_helper "$HOME_DRIFT" verify_chttrans_neutralized >/dev/null

# G2: [EnabledIM] 数字列表回归
sed -i 's/^EnabledIM=$/[EnabledIM]\n0=pinyin\nEnabledIM=/' "$HOME_DRIFT/$CHTTRANS_CONF_RELPATH"
if run_helper "$HOME_DRIFT" verify_chttrans_neutralized >/dev/null 2>&1; then
    fail "校验未拒绝 [EnabledIM] 数字列表回归"
fi
sed -i '/^\[EnabledIM\]$/d; /^0=pinyin$/d' "$HOME_DRIFT/$CHTTRANS_CONF_RELPATH"
run_helper "$HOME_DRIFT" verify_chttrans_neutralized >/dev/null

# G3: 插件禁用条目被移除
sed -i '/^0=chttrans$/d' "$HOME_DRIFT/$FCITX5_CONFIG_RELPATH"
if run_helper "$HOME_DRIFT" verify_chttrans_neutralized >/dev/null 2>&1; then
    fail "校验未拒绝 chttrans 从 [Behavior/DisabledAddons] 移除"
fi

# G4: 插件禁用项先恢复（G3 移除过），再验证顶层 EnabledIM 被填上值时校验拒绝
sed -i 's#^\[Behavior/DisabledAddons\]$#[Behavior/DisabledAddons]\n0=chttrans#' "$HOME_DRIFT/$FCITX5_CONFIG_RELPATH"
sed -i 's/^EnabledIM=$/EnabledIM=pinyin/' "$HOME_DRIFT/$CHTTRANS_CONF_RELPATH"
if run_helper "$HOME_DRIFT" verify_chttrans_neutralized >/dev/null 2>&1; then
    fail "校验未拒绝顶层 EnabledIM 重新赋值"
fi
sed -i 's/^EnabledIM=pinyin$/EnabledIM=/' "$HOME_DRIFT/$CHTTRANS_CONF_RELPATH"
run_helper "$HOME_DRIFT" verify_chttrans_neutralized >/dev/null

# G5: 根级 [DisabledAddons] 节（错误落点，不属于任何选项路径）必须被校验拒绝
sed -i 's#^\[Behavior/DisabledAddons\]$#[DisabledAddons]#' "$HOME_DRIFT/$FCITX5_CONFIG_RELPATH"
if run_helper "$HOME_DRIFT" verify_chttrans_neutralized >/dev/null 2>&1; then
    fail "校验未拒绝根级 [DisabledAddons] 错误形态"
fi
sed -i 's#^\[DisabledAddons\]$#[Behavior/DisabledAddons]#' "$HOME_DRIFT/$FCITX5_CONFIG_RELPATH"
run_helper "$HOME_DRIFT" verify_chttrans_neutralized >/dev/null

# G6: 空的 [EnabledIM] 节头残留（即使没有条目）必须被校验拒绝
sed -i 's/^EnabledIM=$/EnabledIM=\n[EnabledIM]/' "$HOME_DRIFT/$CHTTRANS_CONF_RELPATH"
if run_helper "$HOME_DRIFT" verify_chttrans_neutralized >/dev/null 2>&1; then
    fail "校验未拒绝空 [EnabledIM] 节头残留"
fi
sed -i '/^\[EnabledIM\]$/d' "$HOME_DRIFT/$CHTTRANS_CONF_RELPATH"
run_helper "$HOME_DRIFT" verify_chttrans_neutralized >/dev/null

# G7: 重复的 [Hotkey] 节头（即使都是空的）必须被校验拒绝
printf '[Hotkey]\n' >> "$HOME_DRIFT/$CHTTRANS_CONF_RELPATH"
if run_helper "$HOME_DRIFT" verify_chttrans_neutralized >/dev/null 2>&1; then
    fail "校验未拒绝重复 [Hotkey] 节头"
fi
# 清掉文件末尾多出来的重复节头（保留原有的第一个）
sed -i ':a;$!{N;ba};s/\n\[Hotkey\]$//' "$HOME_DRIFT/$CHTTRANS_CONF_RELPATH"
run_helper "$HOME_DRIFT" verify_chttrans_neutralized >/dev/null

# G8: 重复 [Behavior/DisabledAddons] 节同索引覆盖 —— 文本里仍有 chttrans，
# 但加载向量里没有；校验必须按 parser 语义拒绝
printf '[Behavior/DisabledAddons]\n0=eviladdon\n' >> "$HOME_DRIFT/$FCITX5_CONFIG_RELPATH"
if run_helper "$HOME_DRIFT" verify_chttrans_neutralized >/dev/null 2>&1; then
    fail "校验未拒绝重复节同索引覆盖导致 chttrans 加载失效"
fi
# 重跑安装函数应把漂移收敛回唯一节，校验恢复通过
run_helper "$HOME_DRIFT" disable_chttrans_addon >/dev/null
assert_disabled_vector "$HOME_DRIFT/$FCITX5_CONFIG_RELPATH" eviladdon chttrans
run_helper "$HOME_DRIFT" verify_chttrans_neutralized >/dev/null

# ---------------------------------------------------------------------------
# H. 边界形态：节缺失、CRLF、空文件、顶层 Hotkey= 叶子形态
# ---------------------------------------------------------------------------
# H1: 只有 Engine，无 EnabledIM、无 [Hotkey] —— 必须补出两个空叶子标记
HOME_MISS="$(new_home home-chttrans-missing)"
mkdir -p "$HOME_MISS/.config/fcitx5/conf"
printf 'Engine=OpenCC\n' > "$HOME_MISS/$CHTTRANS_CONF_RELPATH"
run_helper "$HOME_MISS" disable_chttrans_addon neutralize_chttrans_conf >/dev/null
run_helper "$HOME_MISS" verify_chttrans_neutralized >/dev/null
assert_file_contains "$HOME_MISS/$CHTTRANS_CONF_RELPATH" 'Engine=OpenCC'
[ "$(count_lines "$HOME_MISS/$CHTTRANS_CONF_RELPATH" '^EnabledIM=$')" = "1" ] \
    || fail "缺节文件未补顶层 EnabledIM="
[ "$(count_lines "$HOME_MISS/$CHTTRANS_CONF_RELPATH" '^Hotkey=$')" = "1" ] \
    || fail "缺节文件未补顶层 Hotkey="

# H2: CRLF 行尾 —— 规范化行不带 \r，校验通过
HOME_CRLF="$(new_home home-chttrans-crlf)"
mkdir -p "$HOME_CRLF/.config/fcitx5/conf"
printf '# comment\r\nEngine=OpenCC\r\nEnabledIM=pinyin\r\n[EnabledIM]\r\n0=pinyin\r\n[Hotkey]\r\n0=Control+Shift+F\r\n' \
    > "$HOME_CRLF/$CHTTRANS_CONF_RELPATH"
run_helper "$HOME_CRLF" disable_chttrans_addon neutralize_chttrans_conf >/dev/null
run_helper "$HOME_CRLF" verify_chttrans_neutralized >/dev/null
if grep -q $'^EnabledIM=\r' "$HOME_CRLF/$CHTTRANS_CONF_RELPATH"; then
    fail "规范化的 EnabledIM= 带上了 CR"
fi
if grep -q $'^Hotkey=\r' "$HOME_CRLF/$CHTTRANS_CONF_RELPATH"; then
    fail "规范化的 Hotkey= 带上了 CR"
fi

# H3: 空文件（0 字节）—— 补出空标记并保持校验通过
HOME_EMPTY="$(new_home home-chttrans-empty)"
mkdir -p "$HOME_EMPTY/.config/fcitx5/conf"
: > "$HOME_EMPTY/$CHTTRANS_CONF_RELPATH"
run_helper "$HOME_EMPTY" disable_chttrans_addon neutralize_chttrans_conf >/dev/null
run_helper "$HOME_EMPTY" verify_chttrans_neutralized >/dev/null

# H4: 顶层 Hotkey= 叶子（fcitx5 safeSaveAsIni 对空 KeyList 的落盘形态）就是
# 规范形态：叶子输入保持不动，只补缺失的 EnabledIM=，不引入 [Hotkey] 节
HOME_LEAF="$(new_home home-chttrans-leafhotkey)"
mkdir -p "$HOME_LEAF/.config/fcitx5/conf"
printf 'Engine=OpenCC\nHotkey=\n' > "$HOME_LEAF/$CHTTRANS_CONF_RELPATH"
run_helper "$HOME_LEAF" disable_chttrans_addon neutralize_chttrans_conf >/dev/null
run_helper "$HOME_LEAF" verify_chttrans_neutralized >/dev/null
[ "$(count_lines "$HOME_LEAF/$CHTTRANS_CONF_RELPATH" '^Hotkey=$')" = "1" ] \
    || fail "顶层 Hotkey= 叶子未被保留为规范形态"
[ "$(count_lines "$HOME_LEAF/$CHTTRANS_CONF_RELPATH" '^\[Hotkey\]$')" = "0" ] \
    || fail "不应把叶子改写成 [Hotkey] 节"

# H5: 模拟 fcitx5 自动保存后的文件（空键表写为顶层 Hotkey=）重跑必须零改动：
# 不写文件、不产生新备份，否则会形成“每次重跑都变更+备份”的漂移循环
HOME_STABLE="$(new_home home-chttrans-leaf-stable)"
mkdir -p "$HOME_STABLE/.config/fcitx5/conf"
cat > "$HOME_STABLE/$CHTTRANS_CONF_RELPATH" << 'EOF'
# 转换引擎
Engine=OpenCC
# 启用的输入法
EnabledIM=
# 切换键
Hotkey=
OpenCCS2TProfile=
OpenCCT2SProfile=
EOF
STABLE_DIGEST="$(sha256sum "$HOME_STABLE/$CHTTRANS_CONF_RELPATH" | awk '{print $1}')"
run_helper "$HOME_STABLE" disable_chttrans_addon >/dev/null
run_helper "$HOME_STABLE" neutralize_chttrans_conf >/dev/null
[ "$(sha256sum "$HOME_STABLE/$CHTTRANS_CONF_RELPATH" | awk '{print $1}')" = "$STABLE_DIGEST" ] \
    || fail "fcitx5 落盘形态（顶层 Hotkey=）重跑被改动，会形成漂移循环"
if compgen -G "$HOME_STABLE/$CHTTRANS_CONF_RELPATH.bak.*" >/dev/null; then
    fail "已是规范形态的文件不应产生备份"
fi
run_helper "$HOME_STABLE" verify_chttrans_neutralized >/dev/null

# ---------------------------------------------------------------------------
# I. 步骤 5 真实顺序联调：快捷键写入 + chttrans 治理 + 两套校验同文件共存
# ---------------------------------------------------------------------------
HOME_FLOW="$(new_home home-chttrans-flow)"
mkdir -p "$HOME_FLOW/.config/fcitx5/conf"
cat > "$HOME_FLOW/$FCITX5_CONFIG_RELPATH" << 'EOF'
[Hotkey]
EnumerateWithTriggerKeys=True
AltTriggerKeys=Control+Shift+Z

[Behavior]
DefaultPageSize=5
ActiveByDefault=False
# Force Disabled Addons
DisabledAddons=
EOF
run_helper "$HOME_FLOW" \
    write_hotkey_config disable_chttrans_addon neutralize_chttrans_conf \
    verify_hotkey_config ensure_fcitx5_autostart verify_chttrans_neutralized >/dev/null
assert_file_contains "$HOME_FLOW/$FCITX5_CONFIG_RELPATH" 'EnumerateWithTriggerKeys=True'
assert_file_contains "$HOME_FLOW/$FCITX5_CONFIG_RELPATH" 'DefaultPageSize=5'
assert_file_contains "$HOME_FLOW/$FCITX5_CONFIG_RELPATH" 'AltTriggerKeys='
assert_file_contains "$HOME_FLOW/$FCITX5_CONFIG_RELPATH" '[Behavior/DisabledAddons]'
assert_file_contains "$HOME_FLOW/$FCITX5_CONFIG_RELPATH" '0=chttrans'
assert_file_not_contains "$HOME_FLOW/$FCITX5_CONFIG_RELPATH" 'DisabledAddons='
[ "$(count_lines "$HOME_FLOW/$FCITX5_CONFIG_RELPATH" '^ActiveByDefault=True$')" = "1" ] \
    || fail "联调后 ActiveByDefault 未写为目标值"

# ---------------------------------------------------------------------------
# J. 同一轮安装对同一文件多次备份：最早的原始内容必须留得住
# ---------------------------------------------------------------------------
HOME_SAMESEC="$(new_home home-chttrans-samesec)"
mkdir -p "$HOME_SAMESEC/.config/fcitx5/conf"
cat > "$HOME_SAMESEC/$FCITX5_CONFIG_RELPATH" << 'EOF'
[Hotkey]
AltTriggerKeys=Control+Shift+Z

[Hotkey/EnumerateForwardKeys]
0=Control+space

[Behavior]
ActiveByDefault=False
DisabledAddons=
EOF
printf '# 转换引擎\nEngine=OpenCC\n[EnabledIM]\n0=pinyin\n[Hotkey]\n0=Control+Shift+F\n' \
    > "$HOME_SAMESEC/$CHTTRANS_CONF_RELPATH"
cp "$HOME_SAMESEC/$FCITX5_CONFIG_RELPATH" "$WORK_DIR/config-pristine"
cp "$HOME_SAMESEC/$CHTTRANS_CONF_RELPATH" "$WORK_DIR/conf-pristine"
# write_hotkey_config 与 disable_chttrans_addon 都会备份 config；
# 若备份名碰撞，后一次会覆盖前一次，原始内容丢失
run_helper "$HOME_SAMESEC" write_hotkey_config disable_chttrans_addon neutralize_chttrans_conf >/dev/null
mapfile -t CONFIG_BAKS < <(compgen -G "$HOME_SAMESEC/$FCITX5_CONFIG_RELPATH.bak.*" | sort || true)
[ "${#CONFIG_BAKS[@]}" -ge 2 ] || fail "同一轮多次备份 config 只留下 ${#CONFIG_BAKS[@]} 份（碰撞覆盖了早期备份）"
CONFIG_PRISTINE_KEPT=false
for bak in "${CONFIG_BAKS[@]}"; do
    if cmp -s "$bak" "$WORK_DIR/config-pristine"; then
        CONFIG_PRISTINE_KEPT=true
    fi
done
[ "$CONFIG_PRISTINE_KEPT" = true ] || fail "config 最早的原始内容没有在任何备份中留存"
compgen -G "$HOME_SAMESEC/$CHTTRANS_CONF_RELPATH.bak.*" >/dev/null \
    || fail "chttrans.conf 变更前未备份"
cmp -s "$(compgen -G "$HOME_SAMESEC/$CHTTRANS_CONF_RELPATH.bak.*" | sort | head -n 1)" "$WORK_DIR/conf-pristine" \
    || fail "chttrans.conf 备份内容与原文件不一致"
run_helper "$HOME_SAMESEC" verify_hotkey_config verify_chttrans_neutralized >/dev/null

# ---------------------------------------------------------------------------
# K. backup_file 同秒碰撞：已存在同名备份时改用唯一后缀，绝不覆盖
# ---------------------------------------------------------------------------
HOME_COLLIDE="$(new_home home-chttrans-backup-collide)"
mkdir -p "$HOME_COLLIDE/.config/fcitx5/conf"
printf 'target-content\n' > "$HOME_COLLIDE/$CHTTRANS_CONF_RELPATH"
# 预置当前秒与下一秒两个时间戳的备份名，无论函数内部 date 落在哪一秒都会命中碰撞
COLLIDE_S1="$(date +%Y%m%d%H%M%S)"
COLLIDE_NEXT_S="$(( $(date +%s) + 1 ))"
COLLIDE_S2="$(date -d "@${COLLIDE_NEXT_S}" +%Y%m%d%H%M%S)"
printf 'precious-1\n' > "$HOME_COLLIDE/$CHTTRANS_CONF_RELPATH.bak.$COLLIDE_S1"
printf 'precious-2\n' > "$HOME_COLLIDE/$CHTTRANS_CONF_RELPATH.bak.$COLLIDE_S2"
HOME="$HOME_COLLIDE" bash -c '
    set -e
    source "$1"
    backup_file "$2"
' _ "$FUNCTIONS" "$HOME_COLLIDE/$CHTTRANS_CONF_RELPATH" >/dev/null
mapfile -t COLLIDE_BAKS < <(compgen -G "$HOME_COLLIDE/$CHTTRANS_CONF_RELPATH.bak.*" | sort || true)
[ "${#COLLIDE_BAKS[@]}" = "3" ] || fail "碰撞时应新建唯一后缀备份而不是覆盖（现有 ${#COLLIDE_BAKS[@]} 份）"
assert_file_contains "$HOME_COLLIDE/$CHTTRANS_CONF_RELPATH.bak.$COLLIDE_S1" 'precious-1'
assert_file_contains "$HOME_COLLIDE/$CHTTRANS_CONF_RELPATH.bak.$COLLIDE_S2" 'precious-2'
NEW_BAK="$(compgen -G "$HOME_COLLIDE/$CHTTRANS_CONF_RELPATH.bak.*.*" | head -n 1)"
[ -n "$NEW_BAK" ] || fail "碰撞时未生成带唯一后缀的新备份"
assert_file_contains "$NEW_BAK" 'target-content'

# ---------------------------------------------------------------------------
# L. disable 对空/重复 [Behavior/DisabledAddons] 目标节的收敛与校验
# ---------------------------------------------------------------------------
# 断言用顶部定义的 load_disabled_vector（与 fcitx5 parser 一致的合并语义）。

# L1: 已存在的空目标节 —— chttrans 写进该节，不另建新节，索引从 0 连续
HOME_EMPTYDA="$(new_home home-chttrans-empty-da)"
mkdir -p "$HOME_EMPTYDA/.config/fcitx5/conf"
cat > "$HOME_EMPTYDA/$FCITX5_CONFIG_RELPATH" << 'EOF'
[Behavior]
ActiveByDefault=True

[Behavior/DisabledAddons]
EOF
run_helper "$HOME_EMPTYDA" disable_chttrans_addon neutralize_chttrans_conf >/dev/null
run_helper "$HOME_EMPTYDA" verify_chttrans_neutralized >/dev/null
[ "$(count_lines "$HOME_EMPTYDA/$FCITX5_CONFIG_RELPATH" '^\[Behavior/DisabledAddons\]$')" = "1" ] \
    || fail "空目标节场景不应新建重复节"
assert_disabled_vector "$HOME_EMPTYDA/$FCITX5_CONFIG_RELPATH" chttrans

# L2: 重复目标节 —— 合并去重重编号为唯一节，加载向量同时含其它项与 chttrans
HOME_DUPDA="$(new_home home-chttrans-dup-da)"
mkdir -p "$HOME_DUPDA/.config/fcitx5/conf"
cat > "$HOME_DUPDA/$FCITX5_CONFIG_RELPATH" << 'EOF'
[Behavior]
ActiveByDefault=True

[Behavior/DisabledAddons]

[Behavior/DisabledAddons]
0=otheraddon
EOF
run_helper "$HOME_DUPDA" disable_chttrans_addon neutralize_chttrans_conf >/dev/null
run_helper "$HOME_DUPDA" verify_chttrans_neutralized >/dev/null
[ "$(count_lines "$HOME_DUPDA/$FCITX5_CONFIG_RELPATH" '^\[Behavior/DisabledAddons\]$')" = "1" ] \
    || fail "重复目标节未折叠为唯一节"
assert_disabled_vector "$HOME_DUPDA/$FCITX5_CONFIG_RELPATH" otheraddon chttrans
assert_file_contains "$HOME_DUPDA/$FCITX5_CONFIG_RELPATH" '0=otheraddon'
assert_file_contains "$HOME_DUPDA/$FCITX5_CONFIG_RELPATH" '1=chttrans'

# L2a: 阻断场景 —— 首节 0=chttrans 被重复节同索引 0=otheraddon 覆盖，
# 旧行为（早退或只往首节追加）会让加载向量里没有 chttrans；
# 现在必须折叠合并后两者都在向量里
HOME_OVERWRITE="$(new_home home-chttrans-overwrite)"
mkdir -p "$HOME_OVERWRITE/.config/fcitx5/conf"
cat > "$HOME_OVERWRITE/$FCITX5_CONFIG_RELPATH" << 'EOF'
[Behavior]
ActiveByDefault=True
DisabledAddons=

[Behavior/DisabledAddons]
0=chttrans

[Behavior/DisabledAddons]
0=otheraddon
EOF
if HOME="$HOME_OVERWRITE" bash -c 'source "$1"; chttrans_in_disabled_addons "$2"' \
    _ "$FUNCTIONS" "$HOME_OVERWRITE/$FCITX5_CONFIG_RELPATH"; then
    fail "同索引覆盖后 parser-faithful 检测误判为已禁用"
fi
run_helper "$HOME_OVERWRITE" disable_chttrans_addon neutralize_chttrans_conf >/dev/null
run_helper "$HOME_OVERWRITE" verify_chttrans_neutralized >/dev/null
assert_disabled_vector "$HOME_OVERWRITE/$FCITX5_CONFIG_RELPATH" otheraddon chttrans
[ "$(grep -c '=chttrans$' "$HOME_OVERWRITE/$FCITX5_CONFIG_RELPATH" || true)" = "1" ] \
    || fail "阻断场景 chttrans 出现了多次"

# L4: 稀疏索引（0 与 2，缺 1）—— 收敛为 0..n 连续且含全部条目与 chttrans
HOME_SPARSE="$(new_home home-chttrans-sparse)"
mkdir -p "$HOME_SPARSE/.config/fcitx5/conf"
cat > "$HOME_SPARSE/$FCITX5_CONFIG_RELPATH" << 'EOF'
[Behavior/DisabledAddons]
0=first
2=third
EOF
run_helper "$HOME_SPARSE" disable_chttrans_addon neutralize_chttrans_conf >/dev/null
run_helper "$HOME_SPARSE" verify_chttrans_neutralized >/dev/null
assert_disabled_vector "$HOME_SPARSE/$FCITX5_CONFIG_RELPATH" first third chttrans
[ "$(grep -cE '^[0-9]+=' "$HOME_SPARSE/$FCITX5_CONFIG_RELPATH" || true)" = "3" ] \
    || fail "稀疏索引未重编号为连续 0..n"

# L5: 同值重复与同索引后写覆盖 —— 后写胜出、重复值去重、chttrans 补齐
HOME_DEDUP="$(new_home home-chttrans-dedup)"
mkdir -p "$HOME_DEDUP/.config/fcitx5/conf"
cat > "$HOME_DEDUP/$FCITX5_CONFIG_RELPATH" << 'EOF'
[Behavior/DisabledAddons]
0=aaa
1=aaa
2=bbb

[Behavior/DisabledAddons]
0=ccc
EOF
run_helper "$HOME_DEDUP" disable_chttrans_addon neutralize_chttrans_conf >/dev/null
run_helper "$HOME_DEDUP" verify_chttrans_neutralized >/dev/null
assert_disabled_vector "$HOME_DEDUP/$FCITX5_CONFIG_RELPATH" ccc aaa bbb chttrans

# L3: 空/重复 [EnabledIM]、[Hotkey] 节与重复顶层键混合 —— 收敛为唯一双叶子
HOME_MIXDA="$(new_home home-chttrans-mixed)"
mkdir -p "$HOME_MIXDA/.config/fcitx5/conf"
cat > "$HOME_MIXDA/$FCITX5_CONFIG_RELPATH" << 'EOF'
[Behavior]
DisabledAddons=
EOF
cat > "$HOME_MIXDA/$CHTTRANS_CONF_RELPATH" << 'EOF'
EnabledIM=
EnabledIM=pinyin
Hotkey=
[Hotkey]
[EnabledIM]
EOF
run_helper "$HOME_MIXDA" disable_chttrans_addon neutralize_chttrans_conf >/dev/null
run_helper "$HOME_MIXDA" verify_chttrans_neutralized >/dev/null
MIX_CONF="$HOME_MIXDA/$CHTTRANS_CONF_RELPATH"
[ "$(count_lines "$MIX_CONF" '^EnabledIM=$')" = "1" ] || fail "混合形态 EnabledIM= 未收敛唯一"
[ "$(count_lines "$MIX_CONF" '^Hotkey=$')" = "1" ] || fail "混合形态 Hotkey= 未收敛唯一"
[ "$(count_lines "$MIX_CONF" '^\[(EnabledIM|Hotkey)\]$')" = "0" ] || fail "混合形态残留目标节头"


echo "PASS: fcitx5 autostart creation, idempotency, legacy migration, managed repair, foreign-file protection"
echo "PASS: chttrans addon disablement, EnabledIM/Hotkey leaf canonicalization, drift-rejecting verification"
echo "PASS: backup collision avoidance and same-second multi-backup preservation"
