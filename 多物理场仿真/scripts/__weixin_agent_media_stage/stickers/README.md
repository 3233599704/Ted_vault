# Emoji And Sticker Pack

The starter yellow-face entries in `manifest.json` are Unicode emoji and are
sent as text. They are not uploaded as oversized image bubbles.

Custom PNG, JPG, GIF, or WebP artwork can still be registered with a `file`
field. Weixin iLink transports those as image messages because its public
message schema does not expose the native favorite-sticker message type. The
bot only sends files resolved inside this directory.

The legacy PNG files use Twemoji graphics under CC-BY 4.0.
Source: https://github.com/jdecked/twemoji
