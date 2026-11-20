// One tool panel at a time: opening a tool from the rail (or Changes) closes
// whichever other one is open, through that tool's own close (so its module
// tidies up: the sun marker, terrain brushes...). Runs in the capture phase,
// before the tool's own click handler opens it.

const TOOLS = [
  // [the tool's button, its panel, how to close it]
  ['missions-mode', 'missions', 'ms-close'],
  ['factions-mode', 'factions', 'fx-close'],
  ['mapset-mode', 'mapset', 'mapset-close'],
  ['anims-mode', 'animations', 'anims-close'],
  ['sky-mode', 'sky', 'sky-close'],
  ['changes-open', 'changes', 'changes-close'],
  ['hq-mode', 'hq', 'hq-close'],
  ['deck-mode', 'deck', 'deck-close'],
  ['look-mode', 'look', 'look-close'],
  ['limits-mode', 'limits', 'limits-close'],
  ['text-mode', 'text', 'text-close'],
  ['gen-mode', 'gen', 'gen-close'],
  ['sound-mode', 'sound', 'snd-close'],
];

export function exclusivePanels($) {
  const closeOthers = (except) => {
    for (const [button, panel, close] of TOOLS) {
      if (button === except) continue;
      const p = $(panel);
      if (p && !p.hidden) {
        const c = $(close);
        if (c) c.click(); else p.hidden = true;
        const b = $(button);
        if (b) b.classList.remove('on');
      }
    }
    // the terrain tool is a mode, not a panel: turned off too
    const t = $('terrain-mode');
    if (except !== 'terrain-mode' && t && t.classList.contains('on')) t.click();
  };
  for (const id of [...TOOLS.map((t) => t[0]), 'terrain-mode']) {
    const b = $(id);
    if (!b) continue;
    b.addEventListener('click', () => {
      // only when opening it (a click on an open tool's button closes that one)
      const tool = TOOLS.find((t) => t[0] === id);
      const opening = tool ? $(tool[1]) && $(tool[1]).hidden : !b.classList.contains('on');
      if (opening) closeOthers(id);
    }, {capture: true});
  }
}
