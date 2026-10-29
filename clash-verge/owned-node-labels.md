# Owned-node display labels (Verge Rev 2.5.8)

`owned-node-labels.css` is an optional **display-only** theme extension for these exact node identities:

| Node | Added annotation |
| --- | --- |
| SELF-h2 | 外层UDP443 |
| SELF-cdn | XHTTP/TLS · 外层TCP443 |
| SELF-main | REALITY · 外层TCP9443 |

Existing protocol chips, latency values and selection/pin controls remain native. Every existing capability tag after the protocol receives `支持`, preserving its original dynamic text (UDP, XUDP, TFO, MPTCP, SMUX). DIRECT receives only the capability prefix: its outer transport depends on the destination traffic. Other node identities and group cards are excluded.

These transport annotations describe the current native company configuration, not the `udp` flag or a reachability test. Update/remove them if those native paths change. The same names reused in another profile would receive the same annotations; apply only where those names retain these meanings.

## Apply without a core reload

1. Save a private backup of existing Theme settings/CSS. Preserve any existing CSS; append the complete marked block from `owned-node-labels.css` rather than replacing unrelated rules.
2. Open **设置 → 主题设置 → CSS 注入 → 编辑 CSS**. Paste the block into the existing CSS and save the editor (it closes), then save the outer theme dialog.
3. Return to **代理**. The supported theme hook updates `style#verge-theme` live. No subscription reload, node selection, core configuration write or restart is needed.
4. Check both PROXY and FEED, including the existing FEED pin. Inspect the current mini layout and, if used, full-row layout in both color modes. When screenshots are disallowed, visual acceptance requires a screenshot the user chooses to supply; accessibility text alone does not establish clipping/contrast.

Rollback: remove only the `BEGIN quick-deploy owned-node-labels v1` … `END` block in the same native CSS editor and save both dialogs. Restore the prior CSS/theme values if any were separately changed. Do not restore unrelated core/profile files.

## Version-specific selectors

Audited upstream commit: `4337bd85758d54d7b9044b2da3c9ac1176eae1f3` (v2.5.8).

- `src/components/proxy/proxy-item.tsx`: `.MuiListItemText-root` has exact `title=name`; its secondary text contains direct protocol/capability spans.
- `src/components/proxy/proxy-item-mini.tsx`: node Box has exact `title=name + "\n"`; group Boxes also include the current member after that newline, so they do not match. Its child Box contains direct protocol/capability tag elements. The styled Box filters the `component` prop, so the mini selector deliberately works with either divs or spans.
- In both layouts the first direct tag is protocol, and all later direct tags are dynamic capabilities. The extension prefixes all later tags without guessing which flag is second. Full rows use direct spans; mini cards use the verified first-child/sibling relation without assuming a tag name.
- `src/pages/_layout/hooks/use-custom-theme.ts` injects saved CSS, updates it when theme settings change, and supplies `--primary-main` for the current light/dark theme.
- `src-tauri/src/feat/effects.rs` classifies `theme_setting` as `NoEffect` on the networking/core side.

The scoped mini cards grow to fit the annotation and wrap future capability tags; full rows can grow rather than clip. This stylesheet does not modify node names, flags, ports, credentials, routing, TUN or the selected/pinned node. Recheck markup after an application upgrade.
