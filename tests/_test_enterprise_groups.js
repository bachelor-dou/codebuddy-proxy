const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '..', 'dashboard.html'), 'utf8');
// Check every inline script, not just the functions exercised below.
for (const [, attrs, code] of html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)) {
  if (!/\bsrc\s*=/.test(attrs) && !/application\/json/.test(attrs)) new vm.Script(code);
}
const start = html.indexOf('function renderEnterpriseModelsSourceLine(');
const end = html.indexOf("  applyTableSort('enterpriseModelsTable');\n}", start) + "  applyTableSort('enterpriseModelsTable');\n}".length;
const elements = Object.fromEntries(['enterpriseModelCount', 'builtinModelCount',
  'availModelCount', 'modelCatalogFilterCount', 'enterpriseModelsSourceLine', 'enterpriseModelsTable', 'modelsTable']
  .map(id => [id, {innerHTML: '', textContent: ''}]));
const sorts = [];
const context = vm.createContext({
  MODELS_DATA: [{id: 'builtin-test', catalog_type: 'builtin'},
    {id: 'custom:enterprise-test|enterprise-test', catalog_type: 'enterprise',
     name: '<img src=x onerror=alert(1)>', supports_tool_calls: true}],
  document: {getElementById: id => elements[id],
    querySelector: selector => elements[selector.split(' ')[0].slice(1)]},
  window: {VIEW_REALM: 'cn'}, MODEL_CATALOG_FILTER: '', isModelPicked: () => true, applyTableSort: id => sorts.push(id),
  esc: s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]))
});
vm.runInContext(html.slice(start, end), context);
vm.runInContext("function updateAvailModelCount(){ document.getElementById('availModelCount').textContent = '(共 ' + MODELS_DATA.length + ' 个)'; }", context);
vm.runInContext('renderAvailableModels()', context);
assert.match(elements.enterpriseModelsTable.innerHTML, /custom:enterprise-test/);
assert.doesNotMatch(elements.enterpriseModelsTable.innerHTML, /builtin-test/);
assert.match(elements.modelsTable.innerHTML, /builtin-test/);
assert.doesNotMatch(elements.modelsTable.innerHTML, /custom:enterprise-test/);
assert.doesNotMatch(elements.enterpriseModelsTable.innerHTML, /<img/);
assert.equal(elements.enterpriseModelCount.textContent, '(共 1 个)');
assert.equal(elements.builtinModelCount.textContent, '(共 1 个)');
assert.equal(elements.availModelCount.textContent, '(共 2 个)');
assert.deepEqual(sorts, ['modelsTable', 'enterpriseModelsTable']);
vm.runInContext("renderEnterpriseModelsSourceLine({source:'cache'})", context);
assert.match(elements.enterpriseModelsSourceLine.textContent, /缓存/);
vm.runInContext("MODEL_CATALOG_FILTER = 'enterprise-test'; renderAvailableModels()", context);
assert.doesNotMatch(elements.modelsTable.innerHTML, /builtin-test/);
assert.match(elements.enterpriseModelsTable.innerHTML, /enterprise-test/);
vm.runInContext("MODEL_CATALOG_FILTER = 'no-match'; renderAvailableModels()", context);
assert.doesNotMatch(elements.enterpriseModelsTable.innerHTML, /enterprise-test/);
vm.runInContext('MODELS_DATA = []; renderAvailableModels()', context);
assert.match(elements.enterpriseModelsTable.innerHTML, /暂无可用模型/);
assert.match(elements.modelsTable.innerHTML, /暂无可用模型/);
for (const id of Object.keys(elements)) {
  assert.equal([...html.matchAll(new RegExp('id="' + id + '"', 'g'))].length, 1);
}
assert.match(html, /enterpriseModelsTable:\s*\['text'/);
console.log('PASS: script syntax, separate model groups, counts, empty states, sorting, HTML escaping, source status');
