/* Shared classification and matching; no game memory writes. */
const FilterModel = (() => {
  const categories = ['All', 'Weapons', 'Hull', 'Armor', 'Thrusters', 'Reactors', 'Storage', 'Shields', 'Utility', 'Command'];
  const sorts = [
    ['Default','Game order'],['name','Name'],['mass','Mass'],['health','Health'],['cost','Cost (P)'],
    ['area','Size (area)'],['generation','Power generation'],['powerStorage','Power storage'],
    ['resources','Resource storage'],['thrust','Thrust'],['shieldHealth','Shield health'],
    ['dps','Weapon DPS'],['range','Weapon range'],['buildTime','Build time']
  ].map(([key,label])=>({key,label}));
  const bit = (flags, n) => (BigInt(flags) & (1n << BigInt(n))) !== 0n;
  function cleanName(name) { return String(name || '').replace(/\^[0-9]/g, '').trim(); }
  function classify(block, settings = {}) {
    const overrides = settings.categoryOverrides || {};
    if (overrides[block.id]) return overrides[block.id].filter(x => categories.includes(x) && x !== 'All');
    const result = [], f = block.features, name = cleanName(block.name).toLowerCase();
    const weapons = [6, 7, 11, 15, 28].some(n => bit(f, n));
    const utility = [10, 13, 14, 20, 23, 26, 39, 44, 46, 58].some(n => bit(f, n));
    const functional = weapons || utility || [0, 1, 2, 9].some(n => bit(f, n));
    if (weapons) result.push('Weapons');
    if (bit(f, 0)) result.push('Command');
    if (bit(f, 1)) result.push('Thrusters');
    if (bit(f, 2)) result.push('Reactors');
    if (bit(f, 9)) result.push('Shields');
    if (utility) result.push('Utility');
    if (/contain|storage|vault|cargo|resource tank|resource silo|r tank|r silo/i.test(name)) result.push('Storage');
    if (!functional) {
      result.push('Hull');
      if (/armou?r/.test(name) || Number(block.durability) >= (settings.armorDurabilityThreshold || 8)) result.push('Armor');
    }
    return result;
  }
  function matches(block, filter) {
    return (filter.category === 'All' || block.categories.includes(filter.category)) &&
      (filter.source === 'All' || block.source === filter.source) &&
      (!filter.query || `${cleanName(block.name)} ${block.id} ${block.sourceLabel || ''}`.toLocaleLowerCase().includes(filter.query.toLocaleLowerCase()));
  }
  function sortItems(items, metric='Default', direction='desc') {
    const copy=items.slice();
    if(metric==='Default')return copy;
    const sign=direction==='asc'?1:-1;
    return copy.sort((a,b)=>{
      if(metric==='name')return sign*cleanName(a.name).localeCompare(cleanName(b.name),undefined,{numeric:true,sensitivity:'base'}) || a.id-b.id;
      const av=a.stats?.[metric],bv=b.stats?.[metric];
      const aa=typeof av==='number'&&Number.isFinite(av),bb=typeof bv==='number'&&Number.isFinite(bv);
      if(aa!==bb)return aa?-1:1; // Inapplicable stats always go last.
      return (aa?sign*(av-bv):0) || cleanName(a.name).localeCompare(cleanName(b.name),undefined,{numeric:true,sensitivity:'base'}) || a.id-b.id;
    });
  }
  return { categories, sorts, classify, matches, cleanName, sortItems };
})();
if (typeof module !== 'undefined') module.exports = FilterModel;
