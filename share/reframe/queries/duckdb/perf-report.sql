/*
Copyright 2016-2026 Swiss National Supercomputing Centre (CSCS/ETH Zurich)
ReFrame Project Developers. See the top-level LICENSE file for details.

SPDX-License-Identifier: BSD-3-Clause
*/
/*
description: >
  Performance report of a single session against what came before it.

  The baseline is the window of `lookback` days immediately preceding the
  session, anchored on the session's own completion time rather than on the
  current time, so that re-running the report later gives the same answer.
  The session itself is excluded from its own baseline.

  A test case that has no history in the window is still reported, with an
  empty baseline.
params:
  session:
    descr: the session UUID
    required: true
  lookback:
    descr: days of history to compare against
    default: 7
  basename: restrict to this test family
  pvar: restrict to this performance variable
*/
with anchor as (
  select max(job_completion_time_us) as t
  from {{ table }}
  where sess_uuid = $session
), target as (
  select name, sysenv, pvar, punit, avg(pval) as pval, presult
  from {{ table }}
  where sess_uuid = $session and pval is not null
  {% if basename is defined %}
    and basename = $basename
  {% endif %}
  {% if pvar is defined %}
    and pvar = $pvar
  {% endif %}
  group by name, sysenv, pvar, punit, presult
), baseline as (
  select t.name, t.sysenv, t.pvar, t.punit,
         avg(t.pval) as pval, count(*) as samples
  from {{ table }} t, anchor a
  where t.sess_uuid != $session and t.pval is not null
    and t.job_completion_time_us < a.t
    and t.job_completion_time_us >= a.t - CAST($lookback AS BIGINT)*86400000000
  {% if basename is defined %}
    and t.basename = $basename
  {% endif %}
  {% if pvar is defined %}
    and t.pvar = $pvar
  {% endif %}
  group by t.name, t.sysenv, t.pvar, t.punit
)
select target.name, target.sysenv, target.pvar, target.punit,
       target.presult as result,
       target.pval as "pval (mean)",
       baseline.pval as "baseline (mean)",
       baseline.samples as "baseline (samples)",
       round(100.0*(target.pval - baseline.pval)/baseline.pval, 2)
           as "pdiff (%)"
from target left join baseline
     using (name, sysenv, pvar, punit)
order by target.name, target.pvar
