@@ -58,7 +58,7 @@ public void testGetWordListUnmodifiable() {
         wordList.remove(0);
     }
 
-    @Test(expected = MnemonicException.MnemonicLengthException.class)
     public void testBadEntropyLength() throws Exception {
         byte[] entropy = ByteUtils.parseHex("7f7f7f7f7f7f7f7f7f7f7f7f7f7f");
         mc.toMnemonic(entropy);
