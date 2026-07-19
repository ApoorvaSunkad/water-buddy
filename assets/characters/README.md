# Character artwork

Drop your character images here. The app looks for, in this order:

```
assets/characters/<name>/drinking.png
assets/characters/<name>/idle.png
assets/characters/<name>/character.png
assets/characters/<name>/buddy.png
```

Where `<name>` is `female` or `male`.

If none of those exist, the app draws a crude placeholder figure and shows a
warning in the settings window. It will still run — you are never blocked on
having art.

## What the image needs

| Requirement | Why |
|---|---|
| **PNG with a transparent background** | The overlay window is transparent. A white background will show as a white rectangle floating on your desktop. This is the one that actually matters. |
| **Roughly 2:1 to 3:1 tall** (e.g. 600×1200) | The app scales to 260px tall and derives width from the aspect ratio. Very wide images make a very wide window. |
| **At least 520px tall** | Your display runs at 150% scaling, so a 260px-tall character is drawn at 390 physical pixels. Anything smaller will look soft. |
| **Character facing left** | It walks in from the right edge, so facing left means it walks *forward* rather than moonwalking. |
| **Feet at the very bottom of the canvas** | The app aligns the image bottom to the screen bottom. Empty space under the feet becomes a floating character. |

## Removing the background

The reference renders have white backgrounds. To make them transparent:

- **Free, no install:** [remove.bg](https://www.remove.bg) — drop the image in, download the PNG.
- **GIMP:** open the image, `Layer > Transparency > Add Alpha Channel`, then use
  the Fuzzy Select tool on the white area and press Delete.
- **Paint.NET:** Magic Wand the white, press Delete, save as PNG.

Check the result by opening the PNG in a browser — you should see the page
behind it, not white.

## Upgrading to a real walk cycle later

The current animation slides a single static image while bobbing and rocking it,
which reads convincingly as walking. If you later want a true walk cycle, add
numbered frames:

```
assets/characters/female/walk_1.png
assets/characters/female/walk_2.png
...
```

and extend `load_sprite()` in `water_buddy/characters.py` to cycle through them
in the `CharacterWidget.paintEvent` phase. The animation timing already runs
0.0 → 1.0 across the walk, so frame selection is just
`frames[int(phase * len(frames)) % len(frames)]`. Nothing else has to change.
