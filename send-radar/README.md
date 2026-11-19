# Send Radar location shots

This is the offline locator and chapter-transition component used in the first Send Radar episode. It follows an authored camera move into a regional locator for the West Florida Escarpment or Kīlauea. The Florida marker indicates the approximate area rather than an exact animal sighting.

## Render

Use Node.js, Python 3 with Pillow and NumPy, FFmpeg with libx264, and the DejaVu Sans regular and bold fonts. The camera sampler is a standalone ECMAScript module. Rendering runs offline once the Natural Earth land polygons are present.

    curl -fL https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_land.geojson -o ne_50m_land.geojson
    node routes.mjs
    python render_locators.py florida ne_50m_land.geojson florida.mp4
    python render_locators.py hawaii ne_50m_land.geojson hawaii.mp4

The locators are 10 seconds, 1280 by 720 at 24 fps. On desktops where DejaVu Sans is outside the font search path, install the fonts or edit load_font to use their absolute paths. To author another locator, add the route endpoints in routes.mjs and the corresponding destination label in render_locators.py. Confirm the position using the reporting agency for the episode.

## Chapter transitions

The second cut uses dispatch_fx.py to stage Radar’s gesture, animate the wall and mix its sound. The pilot uses a wrist cannon and flying glass, a tether that pulls the screen into a whirlpool, rocket boots that cut a glowing seam, and a prism that separates three panes. A short shutter cut marks the recap. The original four simple wipes remain in transitions.py.

To draw a new cast, install Pillow, NumPy and SciPy, then run the following with a unique episode identifier. Each block of six draws uses every effect once. The saved selection keeps alternate aspect ratios and rerenders consistent.

    python dispatch_fx.py episode-02 --chapters 4 --output transition-cast.json

Use the renderer in the episode studio kit to composite the character’s animated hand and boot positions. The Python module also exposes reveal(mode, old_image, new_image, progress) for use with a pair of same-sized RGB frames.

## Code, geography and footage

camera.mjs is the upstream director camera module from [God’s Eye View](https://github.com/bilawalsidhu/gods-eye-view/blob/6b45ea12cf6206b3ab0956863c4934ecdde97fdf/src/director/camera.js). Copyright (c) 2026 Bilawal Sidhu. See the complete upstream notice in GODS-EYE-VIEW-LICENSE.txt. This package incorporates that module only. The locator geometry is [Natural Earth 1:50m land](https://www.naturalearthdata.com/about/terms-of-use/), a public-domain dataset. The coastline is drawn locally. A production frame carries both the Natural Earth and upstream camera credits.

The remaining locator and transition files are original Send Radar production work. The upstream MIT notice applies to the upstream camera code. Third-party footage retains its own terms. See episode-sources.md for the original media, editorial credits and permissions sources.

Future productions should record the exact download and license, creator, credit line, modification notes and affected chapter for every camera, clip, still, texture and data layer. Put short credits beside the relevant shots and full source links in the video description or production notes. A provider change calls for a new rights check.
