# Sources for the internal adaptations

Contour contains independently written procedures informed by the sources below. The revision identifiers come from the supplied source inventory. Exact checkout bytes and the earlier review logs are not included, so these pins are attribution and retrieval targets, not a claim that their hashes or historical test results have been revalidated. The donor revisions that first informed the original wording remain unestablished.

| Donor and declared revision | Instruction source | License source |
| --- | --- | --- |
| blader/humanizer @ `a32f24407364c74c812b43c15926d638bee1e072` | [Humanizer](https://github.com/blader/humanizer/blob/a32f24407364c74c812b43c15926d638bee1e072/SKILL.md) | [LICENSE](https://github.com/blader/humanizer/blob/a32f24407364c74c812b43c15926d638bee1e072/LICENSE) |
| addyosmani/agent-skills @ `8c99fa36d90068c82d547f7c12a9d67a3e95f4ee` | [Code simplification](https://github.com/addyosmani/agent-skills/blob/8c99fa36d90068c82d547f7c12a9d67a3e95f4ee/skills/code-simplification/SKILL.md) and [debugging and recovery](https://github.com/addyosmani/agent-skills/blob/8c99fa36d90068c82d547f7c12a9d67a3e95f4ee/skills/debugging-and-error-recovery/SKILL.md) | [LICENSE](https://github.com/addyosmani/agent-skills/blob/8c99fa36d90068c82d547f7c12a9d67a3e95f4ee/LICENSE) |
| obra/superpowers @ `22f0b463e7a5e579904dd6211a520321035526f7` | [Systematic debugging](https://github.com/obra/superpowers/blob/22f0b463e7a5e579904dd6211a520321035526f7/skills/systematic-debugging/SKILL.md) | [LICENSE](https://github.com/obra/superpowers/blob/22f0b463e7a5e579904dd6211a520321035526f7/LICENSE) |

The upstream license pages identify MIT licenses and copyright notices for Siqi Chen, Addy Osmani and Jesse Vincent, respectively, dated 2025: [Humanizer](https://github.com/blader/humanizer/blob/main/LICENSE), [agent-skills](https://github.com/addyosmani/agent-skills/blob/main/LICENSE), [Superpowers](https://github.com/obra/superpowers/blob/main/LICENSE). Verify the exact pinned license and retain applicable notices before copying upstream text or resources. The original donor skills and executable resources are not bundled or activated by Contour.

## Adaptation boundaries

| Local procedure | Useful principle | Boundary retained in Contour |
| --- | --- | --- |
| [Documentation and Humanizer](06-api-documentation.md) | Remove filler, clarify the subject and write for the reader | Preserve every technical claim and qualifier; add no facts from memory; verify meaning after editing; allow no change |
| [Implementation and simplification](04-implementation.md) | Improve clarity while preserving behavior | Stay within the assignment, check transformation preconditions and affected consumers, and verify the final change; fewer lines alone are not a benefit |
| [Diagnosis and recovery](04-implementation.md#diagnosis) | Reproduce, form a causal hypothesis, distinguish alternatives and check the original case | Preserve shared work, authority and failed observations; permit authorized incident containment before final diagnosis; do not require new scripts or mandatory TDD sequencing |

The object-to-Map, async-wrapper and sparse-array examples illustrate why simplification needs behavioral preconditions. The cancellation example illustrates why prose editing must preserve the distinction between cancellation and rollback. These are review cases, not retained evidence of an executed comparative study.

When refreshing an adaptation, retrieve and read the complete pinned instructions and examples, verify their license, compare their obligations with the local contract, and retain actual qualification observations. Do not import upstream changes automatically or load donor instructions as competing procedures. Comparative quality, token usage and whole-task efficiency remain unmeasured until an appropriate study supplies evidence.

## Other references

The methodology links primary sources for individual practices, including quality scenarios, compatibility, accessibility, versioned dependency documentation and artifact provenance. Those sources inform Contour's design; they do not certify this package or establish its effectiveness.
