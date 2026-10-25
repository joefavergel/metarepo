"""Wiki bundle support (spec 009): the editorial configuration, layout, bundle checks and the gate.

Reusable by the feed (010), consult (011) and lint (012) specs:

* ``config``   -- ``WikiConfig``: parse/render the configuration block of ``30_wiki/WIKI.md`` and answer
                  "which folder does a page of this type/area live in?" and "which index group is it in?".
* ``bundle``   -- iterate and validate the OKF pages of the bundle; pure text helpers to add an entry to
                  ``index.md`` and ``log.md`` (callers write them under the ``wiki`` lease).
* ``scaffold`` -- plan and create the bundle (never overwrites, P12).
* ``gate``     -- read and change the wiki configuration gate in ``_system/state.json``.
"""
