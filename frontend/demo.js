// Incident summaries for the inspection dialog. The scenario API supplies the incident narrative and model inputs.
// `road` is what the road is really like in the simulation (synthetic, chosen to match the footage). The dispatcher
// does not see it before confirming a response; the playback and the outcome always use it.
export const demoIncidents = [
  {
    id: "disruption",
    title: "Possible crash on CA-47",
    tag: "Unconfirmed",
    mission: "Check for blocked lanes",
    footageId: "crash-lanes-blocked",
    road: "open",
  },
  {
    id: "disruption_b",
    title: "CA-47 closure reports",
    tag: "Multiple reports",
    mission: "Look for blocked lanes",
    footageId: "crash-lanes-blocked",
    road: "blocked",
  },
  {
    id: "congestion",
    title: "Pier 400 exit-road queue",
    tag: "Arrival buffer available",
    mission: "Check whether the exit queue is moving",
    footageId: "terminal-truck-queue",
    road: "blocked",
  },
  {
    id: "flood",
    title: "Water on the connector approach",
    tag: "Depth unknown",
    mission: "Check for water and passing vehicles",
    footageId: "flooded-lane",
    road: "blocked",
  },
];
