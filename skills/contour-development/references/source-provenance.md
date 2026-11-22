# Sources for the internal adaptations

On 22 November 2026 the four donor instructions and the three repository licenses were retrieved from GitHub for the Contour 1.0 review. For each selected file the reviewer compared `git hash-object --no-filters` with `git ls-tree HEAD` and computed SHA-256 over the matching checkout bytes. The following rows identify those exact files at their reviewed revisions.

| Repository and revision | File at that revision | SHA-256 |
| --- | --- | --- |
| blader/humanizer @ `a32f24407364c74c812b43c15926d638bee1e072` | [LICENSE](https://github.com/blader/humanizer/blob/a32f24407364c74c812b43c15926d638bee1e072/LICENSE) | `4ac4810254ab36d45419141aeb8e69bf50652cfafe5b2dab947d06d44e5cbf96` |
| blader/humanizer @ `a32f24407364c74c812b43c15926d638bee1e072` | [SKILL.md](https://github.com/blader/humanizer/blob/a32f24407364c74c812b43c15926d638bee1e072/SKILL.md) | `3013910ef5433dab12f0d2b86d9cf9e3421a253ef986518a68ef07aa29629bdc` |
| addyosmani/agent-skills @ `8c99fa36d90068c82d547f7c12a9d67a3e95f4ee` | [LICENSE](https://github.com/addyosmani/agent-skills/blob/8c99fa36d90068c82d547f7c12a9d67a3e95f4ee/LICENSE) | `6f202f8bd568cd730dbb2b0d1f8e243bc74c2fa1f64dbce9b2c7ea08bd5c9fd7` |
| addyosmani/agent-skills @ `8c99fa36d90068c82d547f7c12a9d67a3e95f4ee` | [skills/code-simplification/SKILL.md](https://github.com/addyosmani/agent-skills/blob/8c99fa36d90068c82d547f7c12a9d67a3e95f4ee/skills/code-simplification/SKILL.md) | `e13b433345e6bf425c3625442ac1b3507b371c18d7ea3e3db822da7a7f742a29` |
| addyosmani/agent-skills @ `8c99fa36d90068c82d547f7c12a9d67a3e95f4ee` | [skills/debugging-and-error-recovery/SKILL.md](https://github.com/addyosmani/agent-skills/blob/8c99fa36d90068c82d547f7c12a9d67a3e95f4ee/skills/debugging-and-error-recovery/SKILL.md) | `95e393819043b50b3ba6f4d89f0fea620d03e560eb52b592b3eb7a41836aaea1` |
| obra/superpowers @ `22f0b463e7a5e579904dd6211a520321035526f7` | [LICENSE](https://github.com/obra/superpowers/blob/22f0b463e7a5e579904dd6211a520321035526f7/LICENSE) | `a37e0e9697144819e1d965176ac4ae5bc3fa02d11e7812036bbcadf6dafe2400` |
| obra/superpowers @ `22f0b463e7a5e579904dd6211a520321035526f7` | [skills/systematic-debugging/SKILL.md](https://github.com/obra/superpowers/blob/22f0b463e7a5e579904dd6211a520321035526f7/skills/systematic-debugging/SKILL.md) | `51c6435d98c83c42c96ba7c35725a0b2c376cd487fb1a4e6bd0ddbe7e33ef202` |

All three repositories use the MIT license. The notices identify Siqi Chen (Humanizer), Addy Osmani (agent-skills) and Jesse Vincent (Superpowers), each dated 2025. Contour ships independently written instructions. Donor skills and their executable resources stay upstream.

## Adaptation review

- The [documentation procedure](06-api-documentation.md) uses the Humanizer practices for removing filler, identifying the reader and preserving qualifiers with technical meaning. Its stricter contract requires a source for each new technical fact. Inspecting its cancellation example confirms that the editor must preserve the distinction between cancellation and rollback.
- The [implementation procedure](04-implementation.md) applies Addy Osmani’s simplification principles within the assigned scope. The reviewer ran three JavaScript counterexamples. The object-to-Map rewrite changed serialized output, removing `async` changed a rejection into a synchronous throw, and replacing a loop with `filter` skipped a sparse slot. The procedure requires verifying the relevant behavior or leaving the code as written.
- The diagnosis procedure retains Superpowers’ causal experiment and the Addy recovery sequence from reproduction through end-to-end verification. Inspection of the local guidance found explicit controls on shared working trees, original-case checks and an exception for authorized incident containment. The review inspected those decisions without executing an incident or benchmarking agent outcomes.

The current pins establish a reproducible basis for these adaptations. Earlier source records contained mutable links and reader-export hashes. The donor commits that informed the original text remain unknown.

## Other references

Architecture, verification, coordination, preparation, delivery and the local execution tools were developed for Contour. References such as [SEI QAW](https://www.sei.cmu.edu/library/quality-attribute-workshops-qaws-third-edition/), [AIP-180](https://google.aip.dev/180) and [W3C evaluation guidance](https://www.w3.org/WAI/test-evaluate/) informed individual practices. Comparative effectiveness remains unmeasured.
