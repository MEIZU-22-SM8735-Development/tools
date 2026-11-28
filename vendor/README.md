# vendor/

Third-party tools the packer calls. They are vendored so that packing does not
depend on a distribution package or on network access.

| path | origin | license |
|---|---|---|
| `mkbootimg/` | AOSP `system/tools/mkbootimg` (`mkbootimg.py` and the `gki/` helpers it imports) | Apache-2.0 |
| `avbtool.py` | AOSP `external/avb` | Apache-2.0 |

`pack-boot.py` uses `mkbootimg.py` to build a header-v4 Android boot image and
`avbtool.py` only to describe the result (`info_image`); it never re-signs the
AVB descriptor, which is why the output keeps the stock metadata and needs an
unlocked device with verification disabled.

Both files carry their own copyright headers. Only the files needed at run
time are included; the upstream tests, build files and the Rust rewrite are
not.
