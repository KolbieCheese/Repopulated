const test = require('node:test');
const assert = require('node:assert/strict');
const model = require('./model');
const block = (features, name = 'Hull', durability = 1) => ({id: 17000, features: String(features), name, durability});
test('hybrid parts belong to each applicable functional category', () => {
  assert.deepEqual(model.classify(block((1n<<6n)|(1n<<2n))), ['Weapons', 'Reactors']);
  assert.deepEqual(model.classify(block(1n<<1n)), ['Thrusters']);
  assert.deepEqual(model.classify(block(1n<<9n)), ['Shields']);
});
test('armor and storage use conservative fallback rules and configurable overrides', () => {
  assert.deepEqual(model.classify(block(0, 'Hull', 8)), ['Hull', 'Armor']);
  assert.deepEqual(model.classify(block(0, 'Hull', 8), {armorDurabilityThreshold: 9}), ['Hull']);
  assert.ok(model.classify(block(0, 'Square Resource Vault XL')).includes('Storage'));
  assert.deepEqual(model.classify(block(0), {categoryOverrides: {'17000': ['Reactors']}}), ['Reactors']);
});
test('category, source and text constraints combine; color tags do not affect search', () => {
  const item = {...block(4, '^2Square Reactor XL'), categories: ['Reactors'], source: 'custom', sourceLabel: 'Reassembler Expanded'};
  const filter = {category: 'Reactors', source: 'custom', query: 'square reactor'};
  assert.equal(model.matches(item, filter), true);
  assert.equal(model.matches(item, {...filter, source: 'faction:8'}), false);
  assert.equal(model.matches(item, {...filter, category: 'Weapons'}), false);
  assert.equal(model.matches(item, {...filter, query: '17000'}), true);
  assert.equal(model.matches(item, {...filter, query: 'EXPANDED'}), true);
  assert.equal(model.matches(item, {...filter, query: 'missing'}), false);
});
test('numeric sorts put missing stats last in both directions and preserve the input', () => {
  const items=[{id:3,name:'C',stats:{mass:null}},{id:2,name:'B',stats:{mass:9}},{id:1,name:'A',stats:{mass:2}}];
  assert.deepEqual(model.sortItems(items,'mass','asc').map(b=>b.id),[1,2,3]);
  assert.deepEqual(model.sortItems(items,'mass','desc').map(b=>b.id),[2,1,3]);
  assert.deepEqual(items.map(b=>b.id),[3,2,1]);
  assert.deepEqual(model.sortItems(items,'Default').map(b=>b.id),[3,2,1]);
});
test('names sort naturally and equal numeric stats use deterministic ties', () => {
  const items=[{id:4,name:'Hull 10',stats:{mass:1}},{id:2,name:'Hull 2',stats:{mass:1}},{id:1,name:'^2Hull 2',stats:{mass:1}}];
  assert.deepEqual(model.sortItems(items,'name','asc').map(b=>b.id),[1,2,4]);
  assert.deepEqual(model.sortItems(items,'mass','desc').map(b=>b.id),[1,2,4]);
});
