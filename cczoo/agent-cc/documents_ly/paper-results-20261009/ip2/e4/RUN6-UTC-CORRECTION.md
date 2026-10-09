# Run 6 UTC correlation correction

Run 6 is `e4p1-a719364175d67653b560bf1b386f8539`.

An initial local draft converted the IP1 ISO timestamps with an incorrect
offset and selected an empty receiver window. That draft was rejected because
the frozen correlation contract requires nonzero request counts and exactly
2507 COMPLETE intervals. It was not used as a result.

The accepted correlation:

- parses the unchanged timestamps as UTC;
- uses unchanged receiver original SHA-256
  `eb72e57098de5bedd719fe64ca328393c4a2ec4fb0c5dc8c37a00b6855570062`;
- counts 7/7 pre-control, 11/11 between slots, and 19/19 post-control requests;
- counts 2507 COMPLETE coverage intervals;
- has SHA-256
  `07ca026944c2f50dbf854da5adba4c5bcefadc129534d698e104904ede12db56`.

The old empty-window verdict is preserved as a rejected historical
determination in the closure narrative; the superseded draft file was not
retained as an authoritative original and is not reconstructed here.
