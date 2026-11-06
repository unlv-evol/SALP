         mc.check(words);
     }
 
     public void testEmptyEntropy() throws Exception {
         byte[] entropy = {};
         mc.toMnemonic(entropy);
