// issue_to_pr_export_contract.qnt
//
// Minimal contract model for issue-to-PR export safety.
// Scope: the critical boundary where a completed run is turned into an exported
// candidate/prediction. This intentionally does not model workflow nodes,
// profiles, visit counters, or full stage control flow.
//
// The strict path models the structured-moderated workflow. The unmoderated
// compatibility path is represented separately so the current Python export
// surface can be checked without weakening the moderated safety contract.

module issue_to_pr_export_contract {

  type Phase = | Init | CandidateObserved | Final

  type ResultStatus =
    | ResultPending
    | ResultCompleted
    | ResultFailed
    | ResultNoPatch

  type GateStatus = | GatePassed | GateFailed

  type Route = | RouteExport | RouteFixup | RouteUnset

  type ProcessStatus = | ProcessWellFormed | ProcessMalformed

  type Readiness = | ReadyVerified | ReadyUnverified | NotReady

  type CandidateState =
    | CandidateReady
    | CandidateFailedWithPatch
    | CandidateAbsent

  type ReuseClass =
    | ReuseMergeCandidate
    | ReuseContinuationCandidate
    | ReuseNone

  type State = {
    phase: Phase,

    // Result and patch boundary.
    resultStatus: ResultStatus,
    patchNonempty: bool,

    // Moderated artifact and RAG boundary.
    moderatedArtifactsPresent: bool,
    ragStatus: GateStatus,
    ragRoute: Route,
    ragProcessStatus: ProcessStatus,
    ragReadiness: Readiness,
    runtimeProof: bool,

    // Row accounting and closure boundary.
    reviewRows: int,
    dispositionRows: int,
    openRows: int,
    weakClosureRows: int,
    malformedArtifacts: bool,

    // Export surface.
    predictionBlanked: bool,
    candidateState: CandidateState,
    reuseClass: ReuseClass,
  }

  pure def initState: State = {
    phase: Init,
    resultStatus: ResultPending,
    patchNonempty: false,
    moderatedArtifactsPresent: false,
    ragStatus: GateFailed,
    ragRoute: RouteUnset,
    ragProcessStatus: ProcessMalformed,
    ragReadiness: NotReady,
    runtimeProof: false,
    reviewRows: 0,
    dispositionRows: 0,
    openRows: 0,
    weakClosureRows: 0,
    malformedArtifacts: false,
    predictionBlanked: true,
    candidateState: CandidateAbsent,
    reuseClass: ReuseNone,
  }

  pure def observed(
    completed: bool,
    patch: bool,
    artifactsPresent: bool,
    rag: GateStatus,
    route: Route,
    process: ProcessStatus,
    readiness: Readiness,
    proof: bool,
    reviewCount: int,
    dispositionCount: int,
    openCount: int,
    weakClosureCount: int,
    malformed: bool,
  ): State = {
    ...initState,
    phase: CandidateObserved,
    resultStatus: if (completed) ResultCompleted else ResultFailed,
    patchNonempty: patch,
    moderatedArtifactsPresent: artifactsPresent,
    ragStatus: rag,
    ragRoute: route,
    ragProcessStatus: process,
    ragReadiness: readiness,
    runtimeProof: proof,
    reviewRows: reviewCount,
    dispositionRows: dispositionCount,
    openRows: openCount,
    weakClosureRows: weakClosureCount,
    malformedArtifacts: malformed,
  }

  pure def rowAccountingOk(s: State): bool =
    s.reviewRows >= 0 and
    s.dispositionRows >= 0 and
    s.openRows >= 0 and
    s.weakClosureRows >= 0 and
    s.reviewRows == s.dispositionRows

  pure def rowsClosedAndProven(s: State): bool =
    rowAccountingOk(s) and
    s.openRows == 0 and
    s.weakClosureRows == 0

  pure def ragAllowsExport(s: State): bool =
    s.ragStatus == GatePassed and
    s.ragRoute == RouteExport and
    s.ragProcessStatus == ProcessWellFormed and
    s.ragReadiness == ReadyVerified

  pure def artifactsWellFormed(s: State): bool =
    s.moderatedArtifactsPresent and
    not(s.malformedArtifacts) and
    rowsClosedAndProven(s)

  pure def canModeratedExport(s: State): bool =
    s.resultStatus == ResultCompleted and
    s.patchNonempty and
    artifactsWellFormed(s) and
    ragAllowsExport(s) and
    s.runtimeProof

  pure def canCompatibilityExport(s: State): bool =
    s.resultStatus == ResultCompleted and
    s.patchNonempty and
    not(s.moderatedArtifactsPresent)

  // Mirrors artifacts.py: legacy unmoderated completed patches may still
  // export, but once moderated artifacts are present the strict gate decides.
  pure def canExport(s: State): bool =
    canCompatibilityExport(s) or canModeratedExport(s)

  pure def finalize(s: State): State =
    if (canExport(s))
      { ...s,
        phase: Final,
        predictionBlanked: false,
        candidateState: CandidateReady,
        reuseClass: ReuseMergeCandidate }
    else if (s.patchNonempty)
      { ...s,
        phase: Final,
        predictionBlanked: true,
        candidateState: CandidateFailedWithPatch,
        reuseClass: ReuseContinuationCandidate }
    else
      { ...s,
        phase: Final,
        resultStatus: ResultNoPatch,
        predictionBlanked: true,
        candidateState: CandidateAbsent,
        reuseClass: ReuseNone }

  pure def goodObserved: State =
    observed(
      true,
      true,
      true,
      GatePassed,
      RouteExport,
      ProcessWellFormed,
      ReadyVerified,
      true,
      2,
      2,
      0,
      0,
      false,
    )

  pure def contractInvariant(s: State): bool =
    (s.phase == Final and not(s.predictionBlanked))
      implies (
        s.candidateState == CandidateReady and
        s.reuseClass == ReuseMergeCandidate and
        canExport(s) and
        (s.moderatedArtifactsPresent implies canModeratedExport(s))
      )

  pure def blankingInvariant(s: State): bool =
    (s.phase == Final and s.candidateState != CandidateReady)
      implies (
        s.predictionBlanked and
        s.reuseClass != ReuseMergeCandidate
      )

  pure def reuseInvariant(s: State): bool =
    (s.reuseClass == ReuseMergeCandidate implies s.candidateState == CandidateReady) and
    (s.reuseClass == ReuseContinuationCandidate implies s.candidateState == CandidateFailedWithPatch) and
    (s.reuseClass == ReuseNone implies s.candidateState == CandidateAbsent)

  pure def inv(s: State): bool =
    contractInvariant(s) and
    blankingInvariant(s) and
    reuseInvariant(s)

  // Small executable harness for model checking.
  var s: State

  action init = s' = initState

  action step = any {
    all {
      s.phase == Init,
      any {
        s' = goodObserved,
        s' = { ...goodObserved, patchNonempty: false },
        s' = { ...goodObserved, runtimeProof: false },
        s' = { ...goodObserved, ragRoute: RouteFixup },
        s' = { ...goodObserved, openRows: 1 },
        s' = { ...goodObserved, reviewRows: 2, dispositionRows: 1 },
        s' = { ...goodObserved, malformedArtifacts: true },
      },
    },
    all {
      s.phase == CandidateObserved,
      s' = finalize(s),
    },
    all {
      s.phase == Final,
      s' = s,
    },
  }

  val modelInvariant = inv(s)

  // Executable tests for the export contract and negative cases.

  run positiveExportCompletesReadyCandidate =
    val out = finalize(goodObserved)
    all {
      assert(canModeratedExport(goodObserved)),
      assert(canExport(goodObserved)),
      assert(out.phase == Final),
      assert(out.resultStatus == ResultCompleted),
      assert(out.candidateState == CandidateReady),
      assert(out.reuseClass == ReuseMergeCandidate),
      assert(not(out.predictionBlanked)),
      assert(inv(out)),
    }

  run emptyPatchNoExport =
    val bad = { ...goodObserved, patchNonempty: false }
    val out = finalize(bad)
    all {
      assert(not(canModeratedExport(bad))),
      assert(not(canExport(bad))),
      assert(out.resultStatus == ResultNoPatch),
      assert(out.candidateState == CandidateAbsent),
      assert(out.reuseClass == ReuseNone),
      assert(out.predictionBlanked),
      assert(inv(out)),
    }

  run ragMustPassWithExportRouteAndVerifiedReadiness =
    val failedGate = { ...goodObserved, ragStatus: GateFailed }
    val fixupRoute = { ...goodObserved, ragRoute: RouteFixup }
    val unsetRoute = { ...goodObserved, ragRoute: RouteUnset }
    val unverified = { ...goodObserved, ragReadiness: ReadyUnverified }
    all {
      assert(not(canModeratedExport(failedGate))),
      assert(not(canModeratedExport(fixupRoute))),
      assert(not(canModeratedExport(unsetRoute))),
      assert(not(canModeratedExport(unverified))),
      assert(not(canExport(failedGate))),
      assert(not(canExport(fixupRoute))),
      assert(not(canExport(unsetRoute))),
      assert(not(canExport(unverified))),
      assert(inv(finalize(failedGate))),
      assert(inv(finalize(fixupRoute))),
      assert(inv(finalize(unsetRoute))),
      assert(inv(finalize(unverified))),
    }

  run runtimeProofRequired =
    val bad = { ...goodObserved, runtimeProof: false }
    val out = finalize(bad)
    all {
      assert(not(canModeratedExport(bad))),
      assert(not(canExport(bad))),
      assert(out.candidateState == CandidateFailedWithPatch),
      assert(out.predictionBlanked),
      assert(inv(out)),
    }

  run rowAccountingOpenRowsAndClosureChecksBlockExport =
    val mismatch = { ...goodObserved, reviewRows: 2, dispositionRows: 1 }
    val open = { ...goodObserved, openRows: 1 }
    val weak = { ...goodObserved, weakClosureRows: 1 }
    all {
      assert(not(rowAccountingOk(mismatch))),
      assert(not(canModeratedExport(mismatch))),
      assert(not(canModeratedExport(open))),
      assert(not(canModeratedExport(weak))),
      assert(not(canExport(mismatch))),
      assert(not(canExport(open))),
      assert(not(canExport(weak))),
      assert(inv(finalize(mismatch))),
      assert(inv(finalize(open))),
      assert(inv(finalize(weak))),
    }

  run malformedArtifactsBlockExport =
    val badProcess = { ...goodObserved, ragProcessStatus: ProcessMalformed }
    val malformed = { ...goodObserved, malformedArtifacts: true }
    all {
      assert(not(canModeratedExport(badProcess))),
      assert(not(canModeratedExport(malformed))),
      assert(not(canExport(badProcess))),
      assert(not(canExport(malformed))),
      assert(inv(finalize(badProcess))),
      assert(inv(finalize(malformed))),
    }

  run compatibilityPathIsExplicitlyUnmoderated =
    val noGate = { ...goodObserved, moderatedArtifactsPresent: false }
    val out = finalize(noGate)
    all {
      assert(canCompatibilityExport(noGate)),
      assert(not(canModeratedExport(noGate))),
      assert(canExport(noGate)),
      assert(out.candidateState == CandidateReady),
      assert(not(out.predictionBlanked)),
      assert(inv(out)),
    }

  run knownBadModeratedStatesMustNotExport =
    val unsetRoute = { ...goodObserved, ragRoute: RouteUnset }
    val noRuntimeProof = { ...goodObserved, runtimeProof: false }
    val openRows = { ...goodObserved, openRows: 1 }
    val malformed = { ...goodObserved, malformedArtifacts: true }
    all {
      assert(not(canModeratedExport(unsetRoute))),
      assert(not(canModeratedExport(noRuntimeProof))),
      assert(not(canModeratedExport(openRows))),
      assert(not(canModeratedExport(malformed))),
      assert(not(canExport(unsetRoute))),
      assert(not(canExport(noRuntimeProof))),
      assert(not(canExport(openRows))),
      assert(not(canExport(malformed))),
    }

  run predictionMustBlankForFailedCandidates =
    val failedWithPatch = finalize({ ...goodObserved, ragRoute: RouteFixup })
    val absent = finalize({ ...goodObserved, patchNonempty: false })
    all {
      assert(failedWithPatch.candidateState == CandidateFailedWithPatch),
      assert(failedWithPatch.predictionBlanked),
      assert(failedWithPatch.reuseClass == ReuseContinuationCandidate),
      assert(absent.candidateState == CandidateAbsent),
      assert(absent.predictionBlanked),
      assert(absent.reuseClass == ReuseNone),
      assert(inv(failedWithPatch)),
      assert(inv(absent)),
    }

  run falseAlarmMayBlockGoodPatch =
    // A good patch with an unresolved review row is allowed to become a
    // continuation candidate. The safety policy tolerates false alarms more
    // than false exports; evals should measure overblocking separately.
    val falseAlarm = { ...goodObserved, openRows: 1 }
    val out = finalize(falseAlarm)
    all {
      assert(not(canModeratedExport(falseAlarm))),
      assert(not(canExport(falseAlarm))),
      assert(out.candidateState == CandidateFailedWithPatch),
      assert(out.reuseClass == ReuseContinuationCandidate),
      assert(out.predictionBlanked),
      assert(inv(out)),
    }

}
