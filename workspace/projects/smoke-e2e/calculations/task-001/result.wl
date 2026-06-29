inputText = $ScriptInputString;
If[inputText === None, inputText = ""];
If[!StringQ[inputText] || StringLength[StringTrim[inputText]] == 0,
  inputText = Quiet[Check[InputString[], ""]];
];
input = Quiet[Check[ImportString[inputText, "RawJSON"], $Failed]];

If[input === $Failed || !AssociationQ[input],
  WriteString[$Output, "{\"error\":\"invalid_input\"}\n"];
  Exit[1];
];

mHpp = N[Lookup[input, "M_Hpp", Missing["M_Hpp"]]];
vDelta = N[Lookup[input, "v_Delta", Missing["v_Delta"]]];

If[!NumericQ[mHpp] || !NumericQ[vDelta] || mHpp == 0,
  WriteString[$Output, "{\"error\":\"invalid_parameters\"}\n"];
  Exit[1];
];

brToy = Times[1.0*^-4, Power[Divide[vDelta, mHpp], 2]];
json = ExportString[<|"BR_toy" -> brToy|>, "RawJSON", "Compact" -> True];
WriteString[$Output, StringTrim[json] <> "\n"];
Exit[0];
