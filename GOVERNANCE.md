# ReFrame Governance

This document describes how the project is governed.
It complements the [Code of Conduct](CODE_OF_CONDUCT.md), the [contribution guidelines](CONTRIBUTING.md) and the [list of maintainers](MAINTAINERS.md).

## Roles

- **Leadership team:** sets the direction of the project and has the final say when there is no consensus.
- **Maintainers:** have write access to the repository and can review and merge pull requests.
- **Contributors:** anyone who has contributed code, documentation, tests, reviews or bug reports.

### Leadership team

Responsibilities:

- Roadmap and release planning
- Decisions on major features and architectural changes
- Adding and removing maintainers
- Representing the project in HPSF

Current members (alphabetically):

- Victor Holanda Rusu ([@victorusu](https://github.com/victorusu)), CSCS
- Vasileios Karakasis ([@vkarak](https://github.com/vkarak)), NVIDIA
- Eirini Koutsaniti ([@ekouts](https://github.com/ekouts)), CSCS
- Guilherme Peretti-Pezzi ([@gppezzi](https://github.com/gppezzi)), CSCS

Members are added by an absolute majority vote of the leadership team and removed by a 2/3 absolute majority.
The member to be removed is notified about the reasons and given the opportunity to respond.

An "absolute majority" means more than half of the current leadership team members, regardless of how many participate in the vote.


### Maintainers

Maintainers review and merge pull requests, triage issues, keep the CI and the release tooling working and take part in the development meetings.
The current list is in [MAINTAINERS.md](MAINTAINERS.md).

Any maintainer can propose a contributor as a new maintainer.
The leadership team decides.
We look for a track record of good contributions, a good understanding of the code base and constructive participation in reviews and discussions.

Maintainers can step down at any time.
A maintainer who has been inactive for more than a year may be removed by the leadership team. Before a vote, the maintainer is notified and given an opportunity to respond. The leadership team decides by majority vote.

## How decisions are made

Most decisions happen in pull requests.
A pull request needs the approval of at least one maintainer other than the author, passing CI and documentation for any user-facing change.

Bigger changes (such as new major features, architectural changes, changes to the public API or the configuration syntax, deprecations, release planning) are discussed in a GitHub issue first, in the development meetings or on Slack.
We decide by consensus.
If we cannot agree, the leadership team votes and a simple majority wins.

Changes to this document needs a 2/3 vote of the leadership team.

## Releases

We follow [semantic versioning](https://semver.org).
Major or minor releases come out typically twice a year.
Patch releases come out when there are fixes to release.

What goes into the next release is tracked in the [GitHub milestones](https://github.com/reframe-hpc/reframe/milestones) and the [release board](https://github.com/orgs/reframe-hpc/projects/1/views/1).

## Meetings and communication

- The maintainers meet every three weeks to go through open issues and pull requests and discuss the next release.
- [GitHub issues](https://github.com/reframe-hpc/reframe/issues) and pull requests are where technical discussions and decisions should be recorded.
- [Slack](README.md#contact) is for user support, announcements and informal discussion.
- Documentation is at https://reframe-hpc.readthedocs.io.

## Security

Dependencies are kept up to date with Dependabot.

## Code of Conduct

Everyone taking part in the project is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
Reports are handled by the leadership team; a member involved in a report does not take part in handling it.
