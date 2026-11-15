// Incident summaries for the inspection dialog. The scenario API supplies the incident narrative and model inputs.
export const demoIncidents = [
  {
    id: "disruption",
    title: "Possible crash on CA-47",
    tag: "Unconfirmed",
    mission: "Check whether a wreck is blocking the road",
    footageId: "crash-lanes-blocked",
  },
  {
    id: "disruption_b",
    title: "CA-47 closure reports",
    tag: "Multiple reports",
    mission: "Look for blocked lanes",
    footageId: "crash-lanes-blocked",
  },
  {
    id: "congestion",
    title: "Pier 400 exit-road queue",
    tag: "Arrival buffer available",
    mission: "Check whether the exit queue is moving",
    footageId: "terminal-truck-queue",
  },
  {
    id: "flood",
    title: "Water on the connector approach",
    tag: "Depth unknown",
    mission: "Check for water and passing vehicles",
    footageId: "flooded-lane",
  },
];
