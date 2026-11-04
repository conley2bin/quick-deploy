/* Native decision logic; this shell owns only handoff-local DOM and draft state. */
(function (global) {
  'use strict';
  const Core = typeof module !== 'undefined' && module.exports ? require('./core.js') : global.CodoxearHandoffCore;
  async function loadNative(appBase, win, importer = url => import(url)) {
    const version = win.CODOXEAR_ASSET_VERSION;
    const [session, display, menu] = await Promise.all([
      importer(new URL('static/app_new_session.js' + (version ? '?v=' + encodeURIComponent(version) : ''), appBase).href),
      importer(new URL('static/app_display.js', appBase).href),
      importer(new URL('static/app_dialog_menu.js', appBase).href),
    ]);
    if (typeof session.createNewSessionController !== 'function' || typeof display.iconSvg !== 'function'
      || typeof menu.createDialogMenuController !== 'function') throw Error('原生模型选择器版本不兼容');
    return { createNewSessionController: session.createNewSessionController, iconSvg: display.iconSvg,
      createDialogMenuController: menu.createDialogMenuController };
  }
  function createTargetModelField({ doc, win, native, menuHost, actionCurrent, onChange }) {
    const abort = new AbortController(), noop = () => {};
    const el = (tag, text = '', attrs = {}) => {
      const n = doc.createElement(tag); n.textContent = text;
      for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
      return n;
    };
    const on = (node, type, fn) => node.addEventListener(type, fn, { signal: abort.signal });
    const element = el('div'), row = el('div', '', { class: 'formGrid newSessionRunConfigRow' });
    const modelField = el('div', '', { class: 'pickerField comboboxField cwdComboboxField' });
    const modelInput = el('input', '', { id: 'handoffModelInput', type: 'text', role: 'combobox', 'aria-autocomplete': 'list',
      'aria-controls': 'handoffModelMenu', 'aria-expanded': 'false', autocomplete: 'off', spellcheck: 'false' });
    const icon = el('span', '', { class: 'cwdComboboxIcon', 'aria-hidden': 'true' }); icon.innerHTML = native.iconSvg('chevronDown'); modelField.append(icon, modelInput);
    const reasoningBtn = el('button', '', { id: 'handoffReasoningBtn', type: 'button', class: 'filePickerBtn dialogPickerBtn sidePickerBtn',
      'aria-haspopup': 'menu', 'aria-controls': 'handoffReasoningMenu', 'aria-expanded': 'false' });
    const reasoningField = el('div', '', { class: 'pickerField comboboxField pickerButtonField' }); reasoningField.append(reasoningBtn);
    for (const [label, node] of [['新会话模型', modelField], ['推理强度', reasoningField]]) {
      const field = el('label', '', { class: 'field' }); field.append(el('span', label, { class: 'fieldLabel' }), node); row.append(field);
    }
    const summary = el('p', '', { id: 'handoffModelSummary', role: 'status' });
    const inherit = el('button', '沿用原会话设置', { id: 'handoffInheritModel', type: 'button' });
    element.append(row, summary, inherit);
    const modelMenu = el('div', '', { id: 'handoffModelMenu', class: 'filePickerMenu dialogPickerMenu cwdSuggestionMenu', role: 'listbox' });
    const reasoningMenu = el('div', '', { id: 'handoffReasoningMenu', class: 'filePickerMenu dialogPickerMenu', role: 'menu' });
    menuHost.append(modelMenu, reasoningMenu);
    const { positionDialogMenu } = native.createDialogMenuController({ windowTarget: win });
    let context = {}, provider = '', effort = '', unavailable = '', browsing = false, menu = '', focus = -1, items = [], disposed = false;
    const d = () => context.draft;
    const display = choice => choice ? ctl.newSessionProviderModelDisplay(choice.model, choice.model_provider) : '';
    function buttonContent(node, text) {
      const content = el('span', '', { class: 'pickerButtonText' }); content.append(el('span', text, { class: 'pickerButtonPrimary' }));
      const chevron = el('span', '', { class: 'pickerButtonChevron', 'aria-hidden': 'true' }); chevron.innerHTML = native.iconSvg('chevronDown');
      node.replaceChildren(content, chevron);
    }
    // Native requires the rest of the New-session dependencies. They are detached
    // and inert; no dialog, spawn, preference setter or application state is used.
    const detached = el('input'); detached.value = '';
    const unreachable = () => { throw Error('接力模型字段不能启动原生会话或修改原生设置'); };
    const ctl = native.createNewSessionController({
      backend: () => 'pi', provider: () => provider, reasoningEffort: () => effort,
      literalModelInputValue: () => '', launchPresetProviderAbsent: () => false,
      sessionCatalog: { get: key => ({ newSessionDefaults: context.data?.new_session_defaults,
        latestSessions: context.data?.sessions || [], tmuxAvailable: false, recentCwds: [] })[key] },
      assignProvider: v => { provider = v; }, assignReasoningEffort: v => { effort = v; },
      assignLiteralModelInputValue: unreachable, assignLaunchPresetProviderAbsent: unreachable,
      modelInput, modelField, reasoningBtn, status: el('p'), setPickerButtonContent: buttonContent,
      renderReasoningMenu: noop, renderModelMenu: noop, setFast: unreachable, setBackend: unreachable, setTmuxChecked: unreachable,
      applyDialogMenus: noop, closeModelMenu: noop, cwdInput: detached, cwdMenu: detached, cwdField: detached, cwdHint: detached,
      nameInput: detached, cwdMenuFocus: () => -1, assignCwdMenuFocus: noop, closeCwdMenu: noop, cwdBrowseAnchor: () => null,
      el: () => detached, resumeMenu: detached, resumeBtn: detached, closeResumeMenu: noop,
      fetchResumeCandidates: unreachable, tmuxToggle: detached, tmuxField: detached, worktreeToggle: detached,
      worktreeInput: detached, worktreeField: detached, startBtn: detached,
    });
    const validItem = item => item && item.providerChoice && item.model && item.model.toLowerCase() !== 'default';
    function catalogError() {
      if (context.loading) return '正在读取原生可用模型';
      if (context.error || !context.data) return '无法读取原生可用模型' + (context.error ? '：' + context.error : '');
      const defaults = context.data.new_session_defaults, pi = defaults?.backends?.pi;
      if (defaults?.warnings && Object.hasOwn(defaults.warnings, 'pi')) return '无法验证原生可用模型：' + (ctl.newSessionDefaultsWarningText() || 'Pi 模型读取已降级');
      if (!pi || !Array.isArray(pi.provider_choices) || !pi.provider_choices.length || !pi.provider_models
        || !pi.provider_choices.some(p => Array.isArray(pi.provider_models[p]) && pi.provider_models[p].some(m => typeof m === 'string' && m.trim() && m.trim().toLowerCase() !== 'default'))
        || !Array.isArray(context.data.sessions) || !ctl.sessionModelOptions().some(validItem)) return '无法读取原生可用模型：原生目录为空或不兼容';
      return '';
    }
    function restoreInput() { modelInput.value = d()?.query ?? display(d()?.choice); browsing = false; }
    function position() {
      if (menu === 'model') positionDialogMenu(modelMenu, modelInput);
      if (menu === 'effort') positionDialogMenu(reasoningMenu, reasoningBtn);
    }
    function applyMenus() {
      modelMenu.hidden = menu !== 'model'; reasoningMenu.hidden = menu !== 'effort';
      modelMenu.classList.toggle('open', menu === 'model'); reasoningMenu.classList.toggle('open', menu === 'effort');
      modelInput.setAttribute('aria-expanded', String(menu === 'model')); reasoningBtn.setAttribute('aria-expanded', String(menu === 'effort'));
      if (menu !== 'model') modelInput.removeAttribute('aria-activedescendant');
      position();
    }
    function close() { menu = ''; focus = -1; restoreInput(); applyMenus(); }
    const usable = () => !disposed && context.visible && !context.locked;
    function guard(captured) {
      return usable() && captured === context && actionCurrent() && captured === context && !disposed;
    }
    function reset(captured) {
      if (!guard(captured)) return;
      Object.assign(d(), { choice: null, query: null, choiceStale: false, effortNote: '' }); close(); paint(); onChange();
    }
    function commit(item, captured) {
      if (!guard(captured) || unavailable || !validItem(item)
        || !ctl.sessionModelOptions().some(v => v.providerChoice === item.providerChoice && v.model === item.model)) return;
      const requested = d().choice?.reasoning_effort || '';
      provider = item.providerChoice; modelInput.value = ctl.newSessionProviderModelDisplay(item.model, provider);
      ctl.setNewSessionReasoningEffort(requested);
      const choice = Core.targetSettings({ model_provider: provider, model: item.model, reasoning_effort: effort });
      if (!ctl.currentReasoningChoices().includes(effort)) {
        // Native's empty-capability fallback is not selectable. Preserve the
        // attempted label as an invalid query so no prior target can submit.
        Object.assign(d(), { query: modelInput.value, choiceStale: true,
          effortNote: '该模型没有原生可选推理强度，请选择其他模型或明确沿用原会话设置' });
        close(); paint(); onChange(); return;
      }
      Object.assign(d(), { choice, query: null, choiceStale: false,
        effortNote: requested && requested !== effort ? `推理强度 ${requested} 不在该模型的原生可选范围，已按原生规则改为 ${effort}` : '' });
      close(); paint(); onChange();
    }
    function renderModels() {
      modelMenu.replaceChildren();
      items = ctl.filteredNewSessionModelOptions().filter(validItem);
      if (!modelInput.value.trim()) items = [null, ...items];
      if (focus < 0) focus = Math.max(0, items.findIndex(v => v && v.model === d().choice?.model && v.providerChoice === d().choice?.model_provider));
      if (focus >= items.length) focus = items.length - 1;
      let previousGroup = ''; const captured = context;
      for (const [i, item] of items.entries()) {
        const group = item?.groupLabel || (!item ? '原会话' : '');
        if (group && group !== previousGroup) { modelMenu.append(el('div', group, { class: 'modelMenuGroup', role: 'presentation' })); previousGroup = group; }
        const title = item ? ctl.newSessionProviderModelDisplay(item.model, item.providerChoice) : '沿用原会话设置';
        const btn = el('button', '', { id: 'handoffModelOption-' + i, type: 'button', class: 'fileMenuItem' + (focus === i ? ' active' : ''),
          role: 'option', 'aria-selected': String(focus === i), 'aria-label': title, title });
        btn.append(el('span', item ? item.model : title, { class: 'fileMenuPath' }));
        if (item) btn.append(el('span', item.providerChoice, { class: 'fileMenuHint' }));
        on(btn, 'mousedown', e => e.preventDefault()); on(btn, 'click', () => item ? commit(item, captured) : reset(captured)); modelMenu.append(btn);
      }
      if (!items.length) modelMenu.append(el('div', 'No matching models', { class: 'pickerEmpty' }));
      if (focus >= 0) modelInput.setAttribute('aria-activedescendant', 'handoffModelOption-' + focus); else modelInput.removeAttribute('aria-activedescendant');
      applyMenus();
    }
    function openModels() {
      const captured = context; if (!guard(captured) || unavailable) return;
      menu = 'model';
      if (d().query == null) { modelInput.value = ''; browsing = true; }
      focus = -1; renderModels();
    }
    function openEfforts() {
      const captured = context; if (!guard(captured) || unavailable || !d().choice || d().query != null || d().choiceStale) return;
      close(); menu = 'effort'; reasoningMenu.replaceChildren();
      for (const value of ctl.currentReasoningChoices()) {
        const btn = el('button', '', { class: 'fileMenuItem' + (effort === value ? ' active' : ''), type: 'button', role: 'menuitemradio', 'aria-checked': String(effort === value), title: value });
        btn.append(el('span', value, { class: 'fileMenuPath' }));
        on(btn, 'click', () => {
          if (!guard(captured) || unavailable || !d().choice || !ctl.currentReasoningChoices().includes(value)) return;
          ctl.setNewSessionReasoningEffort(value);
          d().choice = Core.targetSettings({ ...d().choice, reasoning_effort: effort }); d().effortNote = '';
          close(); paint(); onChange();
        }); reasoningMenu.append(btn);
      }
      applyMenus();
    }
    function blockReason() {
      if (d()?.query != null) return '请从列表选择模型，或选择“沿用原会话设置”';
      if (d()?.choice && unavailable) return unavailable + '；请重新读取或明确选择沿用原会话设置';
      if (d()?.choiceStale) return '所选模型或推理强度已不在原生可用范围，请重新选择';
      return '';
    }
    function paint() {
      modelInput.disabled = !usable() || Boolean(unavailable);
      modelInput.placeholder = unavailable ? unavailable + '；仅可沿用原会话设置' : '沿用原会话设置';
      inherit.disabled = !usable(); reasoningBtn.disabled = !usable() || Boolean(unavailable) || !d()?.choice || d()?.query != null || d()?.choiceStale;
      buttonContent(reasoningBtn, d()?.choice?.reasoning_effort || '沿用原会话设置');
      const choice = d()?.choice;
      summary.textContent = [choice ? `新会话将使用：${display(choice)} · ${choice.reasoning_effort}（仅作用于新会话，原会话不变）` : '新会话将沿用原会话设置',
        d()?.effortNote, blockReason(), unavailable && !choice ? unavailable + '；仅可沿用原会话设置' : ''].filter(Boolean).join('；');
    }
    on(modelInput, 'click', openModels);
    on(modelInput, 'beforeinput', () => { if (usable() && d().query == null) { modelInput.value = ''; browsing = false; } });
    on(modelInput, 'input', () => {
      const value = modelInput.value, captured = context;
      if (!guard(captured) || unavailable) { restoreInput(); return; }
      d().query = value; browsing = false; menu = 'model'; focus = -1; renderModels(); paint(); onChange();
    });
    on(modelInput, 'keydown', e => {
      if (e.key === 'Escape' && menu) { e.preventDefault(); e.stopPropagation(); close(); return; }
      if (e.key === 'Tab') { close(); return; }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault(); if (menu !== 'model') openModels(); else { focus = (focus + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % (items.length || 1); renderModels(); }
      } else if (e.key === 'Enter' && menu === 'model') {
        e.preventDefault(); e.stopPropagation(); if (focus >= 0 && items.length) items[focus] ? commit(items[focus], context) : reset(context);
      }
    });
    on(reasoningBtn, 'click', openEfforts);
    on(reasoningBtn, 'keydown', e => { if (e.key === 'Escape' && menu) { e.preventDefault(); e.stopPropagation(); close(); } });
    on(inherit, 'click', () => reset(context));
    on(doc, 'mousedown', e => { if (!modelField.contains(e.target) && !reasoningField.contains(e.target) && !menuHost.contains(e.target)) close(); });
    on(win, 'resize', position); on(win, 'scroll', position);
    if (win.visualViewport) { on(win.visualViewport, 'resize', position); on(win.visualViewport, 'scroll', position); }
    return { element, close, position, blockReason, target: () => d()?.choice || undefined,
      bind(next) {
        if (disposed) return;
        const different = context.draft !== next.draft || context.seq !== next.seq || context.source !== next.source;
        if (different || !next.visible || next.locked) close();
        context = different ? { ...next } : Object.assign(context, next);
        provider = d()?.choice?.model_provider || ''; effort = d()?.choice?.reasoning_effort || '';
        if (!browsing) restoreInput();
        unavailable = catalogError();
        if (d()?.choice && !unavailable) {
          const input = modelInput.value; modelInput.value = display(d().choice);
          d().choiceStale = !ctl.sessionModelOptions().some(v => v.providerChoice === provider && v.model === d().choice.model)
            || !ctl.currentReasoningChoices().includes(effort); modelInput.value = input;
        }
        paint(); applyMenus();
      },
      dispose() { if (disposed) return; close(); disposed = true; abort.abort(); modelMenu.remove(); reasoningMenu.remove(); element.remove(); },
    };
  }
  const api = { loadNative, createTargetModelField };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.CodoxearHandoffTargetModel = api;
})(typeof window !== 'undefined' ? window : globalThis);
