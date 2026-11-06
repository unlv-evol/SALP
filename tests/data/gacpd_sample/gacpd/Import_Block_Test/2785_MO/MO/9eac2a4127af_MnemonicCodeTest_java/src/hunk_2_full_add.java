@@ -88,7 +88,7 @@ public void testEmptyMnemonic() throws Exception {
         mc.check(words);
     }
 
+    @Test(expected = RuntimeException.class)
     public void testEmptyEntropy() throws Exception {
         byte[] entropy = {};
         mc.toMnemonic(entropy);
