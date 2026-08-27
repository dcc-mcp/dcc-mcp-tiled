# Changelog

## Unreleased

## [0.4.2](https://github.com/dcc-mcp/dcc-mcp-tiled/compare/v0.4.1...v0.4.2) (2026-08-27)


### Bug Fixes

* bind release package and artifact identity ([412475d](https://github.com/dcc-mcp/dcc-mcp-tiled/commit/412475d37ecc64847396817a1f3582a339180bfc))
* harden release asset publication ([827a2f8](https://github.com/dcc-mcp/dcc-mcp-tiled/commit/827a2f86f7a4ff8a6948e1a93aa9e390f56e349f))
* prevent release asset overwrites ([68ac05d](https://github.com/dcc-mcp/dcc-mcp-tiled/commit/68ac05df5f56027f4e32dc6738ce0ea7eea7e6f4))
* reject nonportable release archive paths ([5003fd6](https://github.com/dcc-mcp/dcc-mcp-tiled/commit/5003fd6dcfe2eb7fa9c3c1bee17724290c9e194a))
* revalidate release publication identity ([249edfa](https://github.com/dcc-mcp/dcc-mcp-tiled/commit/249edfa3f6cd656e56446d1ec3507f59abb2a6c8))

## [0.4.1](https://github.com/dcc-mcp/dcc-mcp-tiled/compare/v0.4.0...v0.4.1) (2026-08-25)


### Bug Fixes

* keep release versions synchronized ([e7fcada](https://github.com/dcc-mcp/dcc-mcp-tiled/commit/e7fcada880520472eed54b1852c8f8c273bf7b6e))
* make release contract tests version agnostic ([a2b1069](https://github.com/dcc-mcp/dcc-mcp-tiled/commit/a2b10690779af6756a98c54af4cebd115778bc94))

## [0.4.0](https://github.com/dcc-mcp/dcc-mcp-tiled/compare/v0.3.0...v0.4.0) (2026-08-24)


### Features

* add Tiled install verification ([#5](https://github.com/dcc-mcp/dcc-mcp-tiled/issues/5)) ([1f0cca7](https://github.com/dcc-mcp/dcc-mcp-tiled/commit/1f0cca70d357d8f9ccd1d9d59600c7dc9de4a3c0))

## [0.3.0](https://github.com/dcc-mcp/dcc-mcp-tiled/compare/v0.2.0...v0.3.0) (2026-08-12)


### Features

- Replace the placeholder bridge with typed map, object, tileset, tile-painting,
  validation, inspection, and conversion workflows backed by Tiled's official
  command-line scripting API.
- Add a standalone DCC-MCP server with one bundled `tiled-maps` Skill and ten
  typed tools.


### Security

- Enforce allowed filesystem roots, bounded inputs and outputs, subprocess
  cancellation and deadlines, overwrite opt-in, and atomic output replacement.
- Keep the bundled JavaScript driver fixed; callers cannot submit source code.


### Testing

- Add unit, Skill-schema, packaging, Python 3.9/3.12, and pinned Tiled 1.12.2
  native end-to-end release gates.


### Features

* ship production-ready Tiled authoring ([#2](https://github.com/dcc-mcp/dcc-mcp-tiled/issues/2)) ([ccb9b28](https://github.com/dcc-mcp/dcc-mcp-tiled/commit/ccb9b28a17420d9659ef92fd65f269e20536399e))

## [0.2.0](https://github.com/dcc-mcp/dcc-mcp-tiled/compare/v0.1.0...v0.2.0) (2026-07-24)


### Features

* add DCC MCP adapter ([fc4cb6b](https://github.com/dcc-mcp/dcc-mcp-tiled/commit/fc4cb6b5cf82b19e700f5294d41c92328f52ee17))
* add GIMP MCP adapter ([4fcda51](https://github.com/dcc-mcp/dcc-mcp-tiled/commit/4fcda51b29d551e59175a81fa4fafcea5d2e8252))


### Bug Fixes

* keep persistent GIMP bridge process alive ([441aa0c](https://github.com/dcc-mcp/dcc-mcp-tiled/commit/441aa0c8d7a8c2bf3178457e931ec2835115061c))
* match GIMP plugin folder to module name ([63799cb](https://github.com/dcc-mcp/dcc-mcp-tiled/commit/63799cbf1c60e53c7c56367b01b54d6290af76fb))
* use GIMP persistent procedure callback signature ([9489a4f](https://github.com/dcc-mcp/dcc-mcp-tiled/commit/9489a4f452b5d0bd947a3108b761ba26b0256aff))
* verify GIMP AppImage checksum ([573314b](https://github.com/dcc-mcp/dcc-mcp-tiled/commit/573314bc3bf1daf6e1e10ea722d03f66927a2eaa))


### Documentation

* optimize workflow showcase ([4ba9018](https://github.com/dcc-mcp/dcc-mcp-tiled/commit/4ba9018a13d6804dda3fcc3818b1dce1b95f78e3))
* redesign DCC-MCP brand visuals ([52de749](https://github.com/dcc-mcp/dcc-mcp-tiled/commit/52de74985ffbbd83ce0bc79cdafd70b0b585bf1d))

## 0.1.0

- Initial Tiled session bridge and MCP adapter.
